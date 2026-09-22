# CleanCarry architecture

## Product boundary

CleanCarry is an audit-first research and execution stack for same-venue Hyperliquid spot/perpetual
carry. It discovers candidates, estimates cost-aware carry, preserves research evidence, and can
route explicitly armed, tightly capped paired orders through an isolated live adapter.

The target position is:

```text
long canonical USDC spot + short equal-dollar perpetual
```

This is directionally delta-neutral at entry, not risk-free. Funding can reverse, basis can move,
books can gap, one leg can become unavailable, and spot/perpetual operational rules can diverge.

## Runtime shape

```text
                         Hyperliquid public POST /info
                                      |
                              HyperliquidInfoClient
                                      |
                +---------------------+---------------------+
                |                     |                     |
          opportunities             status                funding
                |                     |                     |
        universe intersection   account normalization   realized funding
                |
      funding history + L2 books
                |
       CarryMarket normalization
                |
      cost-aware eligibility/score
                |
       terminal table + archives
                |
      data/raw/ and data/derived/
```

`systemd` timers invoke the `opportunities` and `status` commands on Lubuntu. They are schedulers,
not business-logic layers.

Historical replay is a separate, read-only path:

```text
data/derived/carry_markets_*.parquet
                 |
       strict schema + UTC/as-of filter
                 |
       +---------+----------+
       |                    |
    ensemble          current-only
       |                    |
       +---------+----------+
                 |
        hysteresis replay
                 |
   spot + perp + funding - costs
                 |
 manifest.json + summary.json + trade Parquet
```

The autonomous research boundary is read-only/paper, with a separate direct live adapter:

```text
all venue perps -> canonical-USDC hedge map -> explicit filter reasons
                -> eligible survivors -> deterministic rank and allocation
                -> read-only intentions OR shadow paired fills
                -> persistent positions -> hedge monitor
                -> hysteresis exit -> same-cycle redeployment
```

`cleancarry autonomous --mode read_only` stops at intentions. `--mode shadow` changes only local
paper state. The separately armed `cleancarry live` path uses an API-wallet signer, persistent live
state, hard caps, paired IOC execution/recovery, and account reconciliation.

## Module responsibilities

| Module | Owns | Must not own |
| --- | --- | --- |
| `config.py` | Environment parsing, defaults, runtime directories, explicit aliases | Network access or scoring logic |
| `hyperliquid.py` | Thin public `/info` requests and transport lifetime | Strategy decisions, persistence, or signing |
| `scanner.py` | Market intersection, normalization, funding features, spreads, gates, ranking | CLI formatting or order execution |
| `models.py` | Stable in-process records for markets and opportunities | API payload parsing |
| `archive.py` | Timestamped Parquet snapshots and deduplicated time series | Trading decisions |
| `account.py` | Read-only normalization of perp positions and spot balances | Portfolio targets or reconciliation actions |
| `cli.py` | Command orchestration, human-readable output, archive calls | Venue-specific parsing or hidden strategy state |
| `research/accounting.py` | Paired-leg dollar P&L identity | Signals, I/O, or execution |
| `research/replay.py` | Signal preparation and chronological hysteresis replay | Archive discovery or study persistence |
| `research/studies.py` | Strict archive loading, hashes, GO/KILL rule, atomic study artifacts | Live collection or order execution |
| `paper.py` | Portfolio selection, shadow paired execution, paper state, reconciliation, controls | Venue signing or live order transport |
| `live.py` | API-wallet signing, paired live orders/recovery, persistent arming state | Signal forecasting or read-only collection |

## Opportunity pipeline

1. Fetch perpetual metadata/contexts, spot metadata/contexts, and predicted funding.
2. Build the intersection using markets quoted by the canonical USDC token and matching perpetual
   names. Hyperliquid's market-level `isCanonical` flag is naming metadata; most valid `@index`
   USDC markets set it false.
3. Apply the configured spot-to-perpetual alias map where HyperCore names differ.
4. Join spot contexts to metadata by the context's returned `coin` identifier. Context arrays may
   contain additional indexed entries and are not assumed to be positionally aligned.
5. Rank the initial intersection by current funding and liquidity so expensive history/book/candle
   calls
   are spent on plausible names.
6. For configured candidate counts, fetch funding history, five days of daily candles, and both
   books; derive average daily dollar volume, spread, and conservative two-sided USD depth inside
   the configured impact band.
7. Normalize each valid pair into `CarryMarket`.
8. Compute a simple expected hourly funding rate and convert it to gross APR.
9. Subtract amortized round-trip fees/spreads and the configured basis-risk buffer.
10. Apply conservative eligibility gates and persist explicit reasons for every discovered perp.
11. Rank only eligible survivors by expected net APR.
12. Display results and, by default, archive raw-normalized and derived records.

The upstream calls are sequential observations, not an atomic market snapshot. Each archived file
records collection time, but consumers must not assume all fields were observed simultaneously.

## Autonomous shadow contract

- Existing positions use the lower exit hurdle; new positions require the higher entry hurdle.
- Selection is deterministic by descending expected net APR, then coin name.
- Notional is capped by book/volume capacity, per-asset limit, total deployment, strategy capital,
  modeled perpetual collateral, margin utilization, and available position slots.
- The deployment limit is divided into 20 equal slots by default. Fewer qualifying pairs leave
  unused cash rather than concentrating capital into the remaining names.
- A paired shadow entry is accepted only when both simulated fills exist and their notional mismatch
  is inside the hedge tolerance. Matched partial fills may open a smaller hedged position.
- Unmatched fills are flattened in simulation. Failed recovery persists unresolved exposure and
  enters `SAFE_MODE`, which blocks new entries.
- Hedge correction requires both the basis-point tolerance and dollar trade buffer to be exceeded.
- Exits happen before entries so released capital can be redeployed in the same cycle.
- Atomic `state/paper_state.json` is the restart checkpoint; `state/paper_ledger.jsonl` is the
  append-only audit trail. Derived strategy-cycle snapshots preserve inspectable decisions.
- Target resizing uses a percentage no-trade region. Once deviation exceeds half the configured
  full buffer, both legs move only to the corresponding buffer edge.
- Operator pause/safe modes prevent new entries. Close requests execute on the next cycle.

CleanCarry owns universe requirements, forecasting, eligibility, ranking, allocation, hysteresis,
desired exposures, and strategy limits. A future Conductor integration may own scheduling,
supervision, invocation, and approved execution routing, but it must call this boundary rather than
reimplement the strategy.

## Financial definitions and units

The production expected hourly funding estimate is:

```text
arithmetic mean(last 96 hourly funding observations)
```

This implements the four-day funding forecast in the supplied RobotWealth basis document. The older
current/predicted/EWMA ensemble remains a named legacy replay comparator. Eligibility fails closed
when predicted funding, required history, five-day volume, or observed spreads/depth are missing.

```text
gross funding APR = expected hourly funding * 8,760

round-trip cost bps = configured round-trip fees
                    + observed spot spread
                    + observed perpetual spread

cost APR = (round-trip cost bps / 10,000)
         * (8,760 / expected holding hours)

expected net APR = gross funding APR
                 - cost APR
                 - basis-risk buffer APR

basis bps = (perpetual mid / spot mid - 1) * 10,000
```

APR is a simple annualized comparison measure, not a realized return forecast or a compounded yield.
The calculation currently treats observed full spreads as a conservative round-trip cost proxy.

## Data model and storage

Runtime data is local and intentionally simple:

```text
data/raw/
  perp_contexts_<UTC>.parquet
  spot_contexts_<UTC>.parquet
  predicted_fundings_<UTC>.parquet
  funding_history_<COIN>.parquet
  daily_volume_<COIN>.parquet
  account_summary_<UTC>.parquet
  perp_positions_<UTC>.parquet
  spot_balances_<UTC>.parquet
  user_funding.parquet

data/derived/
  carry_markets_<UTC>.parquet
  opportunities_<UTC>.parquet
  studies/<STUDY_ID>/
    manifest.json
    summary.json
    trades_ensemble.parquet
    trades_current_only.parquet
```

Timestamped snapshots are append-by-new-file. Funding histories and realized user funding are
upserted into deduplicated time-series files. These files are ignored by Git; the `.gitkeep` files
only preserve directory shape.

`data/raw` means normalized source observations, not byte-for-byte HTTP response bodies. If exact
payload replay becomes necessary, add a separately named immutable payload layer and record the
schema/provenance decision in an ADR rather than changing the meaning of `raw` silently.

## Historical replay contract

The first M2 slice replays exactly one coin. `observed_at_utc` is the decision clock, and an optional
inclusive `as_of_utc` cutoff is applied before any signal is prepared. Duplicate coin/timestamp rows
are rejected as ambiguous. Both compared models receive the same filtered price path and cost
assumptions:

- the ensemble uses the production funding blend and its required-input gates;
- the current-only baseline uses current funding with the common liquidity, basis, and spread gates;
- entry requires the configured entry APR hurdle and eligibility;
- exit occurs on ineligibility or when signal APR falls below the configured exit hurdle;
- funding over an interval uses only the rate and perpetual price known at the interval start;
- spot and short-perpetual quantities are fixed from equal-dollar entry notionals;
- quoted spreads approximate round-trip slippage, and configured fees are charged once per trade;
- if an exit spread is unavailable, the corresponding entry spread is reused and disclosed.

Every completed study writes its manifest last. The manifest records schema version, hypothesis,
decision rule, configuration, package/source hashes, input file hashes and coverage, output hashes,
model summaries, and limitations. `INSUFFICIENT_DATA` is distinct from `GO` and `KILL`.

## Safety and correctness invariants

- Signing and order submission are isolated in `live.py`; read-only and shadow paths do not construct
  the signer or exchange client.
- `LIVE_TRADING_ENABLED` alone is insufficient: persistent arming bound to the configured account is
  also required.
- Invalid/non-positive mids are dropped before scoring.
- Unknown symbol relationships are excluded unless configured through the alias map.
- Eligibility fails closed for required data that could not be observed.
- Account reads require only a public address.
- UTC is canonical for filenames and observation timestamps.
- Historical replay must define a decision timestamp and prohibit observations after it.
- Stored schemas and financial units must be migrated explicitly when changed.
- Live execution fails closed on unresolved actions or managed-position reconciliation mismatches.
- Read-only and shadow use the same filter/rank/select logic; shadow adds only local simulated state.
- The RobotWealth-aligned production forecast is `rw_mean_96h`; `legacy_ensemble` is research-only.

## Failure behavior

Top-level universe and predicted-funding calls currently fail the command on HTTP errors. Per-name
history and book failures are isolated: the affected values become unavailable and the candidate
fails eligibility. This preserves broad scan usefulness without pretending missing evidence is zero
cost or zero risk.

The current client has a request timeout but no explicit retry/backoff or response-schema versioning.
Those are known operational gaps, not implicit guarantees.

## Planned evolution seams

- Historical replay should consume archived observations and emit separate study artifacts; it
  should not mutate the live scanner into a backtester. The first single-pair slice now follows this
  boundary; portfolio-wide replay and richer diagnostics remain future work.
- Paper portfolio state should live behind a ledger/reconciliation abstraction, separate from
  `account.py` snapshots.
- Execution, if approved, should be a new boundary with a signer isolated from research code and a
  venue interface that makes idempotency, paired-leg state, and recovery explicit.
- Portfolio limits and arming state belong in persistent control state, not ad hoc CLI flags.

Milestone promotion and gates are defined in `ROADMAP.md`. Durable changes to these boundaries are
recorded in `docs/adr/`.
