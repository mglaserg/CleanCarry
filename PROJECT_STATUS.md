# CleanCarry project status

**Canonical status date:** 2026-09-22
**Current release:** 0.1.0  
**Current milestone:** M2 — Historical carry simulator  
**Baseline commit:** `b3fb18c` (`init Git`), plus the current documentation working tree

This file answers **what is true now**. `ROADMAP.md` answers **what comes next**.

## North star

CleanCarry should answer:

> Which same-venue spot/perpetual carry candidates remain economically attractive after observable
> costs and risk, how reliably does that attractiveness persist, and can the full position be
> operated and reconciled safely?

## Implemented now

### Scanner and research inputs

- Dynamic Hyperliquid perpetual and canonical-USDC-quoted spot discovery, including documented
  `@index` markets whose market-level `isCanonical` naming flag is false.
- A persisted record for every discovered perpetual, including explicit no-spot, volume,
  open-interest, history, spread, depth, basis, funding, net-carry, and override rejection codes.
- Explicit HyperCore spot/perpetual alias configuration, defaulting to `UBTC:BTC`.
- Current and predicted funding, RobotWealth four-day arithmetic-mean forecast, legacy 24h/72h
  EWMAs, funding volatility, basis, five-day average dollar volume, spread, and bounded depth.
- RobotWealth-aligned 30%/10% net-APR hysteresis, equal-slot nominal sizing, 20% modeled perp
  collateral, and a percentage no-trade buffer that resizes only to its edge.
- Conservative eligibility behavior when required history, prediction, or spread data is missing.

### Data and account observation

- Timestamped local Parquet snapshots for normalized market, opportunity, account, position, and
  balance records.
- Deduplicated time-series archives for funding history and realized user funding.
- Read-only account inspection using a public address.
- Generated data, logs, state, `.env`, and credentials excluded from Git.
- Collision-resistant atomic Parquet writes for snapshots and deduplicated time series.

### Operations and safety

- CLI commands for opportunities, account/funding observation, replay, autonomous read-only and
  shadow cycles, paper status/control, and the hard-stop `live` command.
- Lubuntu collection timers and a hardened long-running autonomous shadow service.
- A `uv`-managed Lubuntu installer using a committed lockfile, with upgrade-safe preservation of
  operator configuration and runtime evidence.
- Direct Hyperliquid API-wallet execution is implemented behind environment and persistent arming,
  hard live caps, paired-leg recovery, reconciliation, and safe mode; it was not live-tested.

### Autonomous shadow foundation

- Shared read-only/shadow pipeline for filter, rank, select, size, paired simulation, monitor, exit,
  and same-cycle redeployment.
- Deterministic capital allocation with position, single-asset, total-deployment, strategy-capital,
  liquidity-capacity, modeled collateral, and margin-utilization limits.
- Entry/exit hysteresis and dollar/basis-point hedge-rebalance buffers.
- Atomic schema-versioned paper state and append-only JSON-lines audit events.
- Idempotent paired actions, partial hedged fills, unmatched-leg flatten recovery, unresolved-exposure
  safe mode, restart without duplicate entries, and explicit reconciliation checks.
- Paper controls for pause, resume, safe mode, close-one, close-all, and kill-on-next-cycle.

### Historical replay foundation

- Strict loader for selected-coin `carry_markets_*.parquet` snapshots.
- Inclusive, timezone-aware as-of cutoff applied before signal preparation.
- Duplicate timestamp rejection and required-schema validation.
- Single-pair entry/exit replay with configured APR hysteresis.
- Explicit spot, short-perpetual, interval-funding, fee, spread/slippage, and net P&L attribution.
- Ensemble comparison against a current-funding-only baseline under shared market/cost inputs.
- Versioned study directory with atomic JSON writes, input/output SHA-256 identities, source-tree
  identity, configuration, limitations, summaries, and a pre-registered GO/KILL rule.

## Verification

On 2026-09-22 using Python 3.12.3:

- `pytest`: **30 passed**;
- `ruff check .`: **passed**;
- `uv 0.12.17`: lock, sync, test, lint, and CLI wiring passed; the unarmed live command was verified
  to fail closed before constructing an execution cycle;
- study persistence and ensemble/current-only comparison were exercised with synthetic Parquet
  archives, including an excluded future observation;
- root and `autonomous` CLI help smoke checks passed after editable installation with declared
  runtime/development dependencies.
- autonomous tests exercised filter/rank/select, limits, hysteresis hold/exit, replacement,
  partial/rejected legs, recovery, idempotency, hedge correction, stale data, restart, and
  reconciliation safe mode.
- three pre-alignment mainnet public-data shadow cycles completed without transmitting orders. The first
  exposed and the next two verified fixes for market-level canonical-flag handling and spot-context
  alignment. The final cycle discovered 234 perps, evaluated nine explicit/exact USDC spot hedges,
  found zero entries above configured gates, and persisted the full rejection evidence. HYPE was
  structurally valid but below the configured 10% net-APR entry hurdle at roughly 8.15%.
- one RobotWealth-aligned mainnet shadow cycle then completed without transmitting orders. It
  discovered 234 perps, evaluated nine explicit/exact USDC spot hedges, and found zero entries at
  the 30% net-APR gate. Of the two pairs that passed the $50 million five-day average-volume gate,
  HYPE measured about 16.46% gross/3.15% expected net APR and BTC about 13.09% gross/0.54% net APR.

No authenticated/live-execution Hyperliquid smoke test or Lubuntu/systemd install test was run.
Public mainnet `/info` retrieval was exercised only in read-only and shadow modes. Do not interpret
unit-test success as live-execution or operational verification.

## What is incomplete

- The runtime data directories now contain four real autonomous scan cycles, which is far too little history
  to run or promote the M2 study.
- No portfolio-wide archived-data coverage/cadence/gap audit exists; validation is currently scoped
  to files contributing observations for the selected coin.
- Snapshots from one scan are not tied together by a run ID or manifest.
- Stored records have no explicit schema version or migration system.
- The RobotWealth parameters are source-grounded defaults but have not yet been validated on the
  repository's own retained Hyperliquid history; the basis-risk buffer remains an assumption.
- The spread/fee model is intentionally simple; shadow fills are deterministic rather than a
  calibrated impact or queue-position model.
- API requests have timeouts but no explicit retry/backoff or rate-limit policy.
- Paper state is single-process and lacks locking, backup/restore drills, and crash injection between
  every ledger/state write.
- Funding accrual, fee debits, daily P&L, and public-account-to-paper reconciliation are not yet
  integrated into each autonomous cycle.
- Tests do not yet cover archive interruption, account normalization, CLI rendering, broad malformed
  payloads, exact funding settlement timing, or elapsed order-timeout behavior.
- The autonomous service has not completed the sustained shadow evidence window required by M3.
- Live trading code now exists at explicit user direction, but no real order was sent and the M2/M3
  evidence gates remain incomplete. Running it before those gates accepts the risk documented in
  ADR 0006.

## Current objective

M2 remains the active evidence gate. Run and harden it on real archived observations, then burn in
the new shadow foundation without skipping milestone promotion:

1. collect or locate sufficient `carry_markets` history for at least one eligible coin;
2. add portfolio-wide coverage, cadence, gap, and schema auditing before study execution;
3. validate interval-funding assumptions against actual settlement timestamps/payments;
4. add holding-period, turnover, mark-to-market drawdown, and tail diagnostics;
5. add at least one simpler baseline and sensitivity analysis for costs/hurdles;
6. execute the registered study and retain its first real `GO`, `KILL`, or `INSUFFICIENT_DATA`
   result without changing the rule after seeing the outcome.
7. integrate autonomous funding/P&L reconciliation and retain a sustained shadow run with no
   unexplained cash, position, hedge, or restart drift.

## Recommended next files

Continue within the separate research and paper boundaries rather than putting portfolio state into
`scanner.py`:

```text
src/cleancarry/research/     implemented single-pair slice
  replay.py                  signal preparation and chronological replay
  accounting.py              paired-leg P&L identity
  studies.py                 archive loading and reproducible artifacts
tests/
  test_replay.py             as-of, replay, and manifest coverage
  test_accounting.py         attribution identity coverage
  test_paper.py              lifecycle, failure, restart, and reconciliation coverage
src/cleancarry/paper.py      shadow portfolio, paired simulation, state, and reconciliation
```

The study artifact contract is recorded in ADR 0003. Extend it compatibly or supersede the ADR when
requirements force a breaking semantic change.

## Handoff checklist

A fresh session should:

1. read `AGENTS.md`, this file, `ROADMAP.md`, and `ARCHITECTURE.md`;
2. run tests and lint before changing behavior;
3. keep research/shadow work isolated from the separately armed live execution boundary;
4. run and harden replay against real archives, then burn in shadow mode;
5. do not mark M3 complete without retained sustained reconciliation evidence;
6. update this file, `ROADMAP.md`, and `CHANGELOG.md` with the result.
