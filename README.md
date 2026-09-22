# CleanCarry v0.1 — Research and autonomous shadow foundation

**Lubuntu + Hyperliquid. Read-only venue access; persistent shadow execution only.**

CleanCarry looks for same-venue delta-neutral carry candidates:

`LONG Hyperliquid spot + SHORT equal-dollar Hyperliquid perp`

CleanCarry does **not** send orders. It now runs the full filter/rank/select/size/monitor/exit/redeploy
decision lifecycle in read-only or local shadow mode while the historical and paper evidence gates
remain open.

## Project documentation

- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — current truth, verification, gaps, and immediate work.
- [`ROADMAP.md`](ROADMAP.md) — capability milestones and promotion gates.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — boundaries, runtime flow, data model, units, and invariants.
- [`AGENTS.md`](AGENTS.md) — operating contract for coding agents and contributors.
- [`CHANGELOG.md`](CHANGELOG.md) — concise history of meaningful changes.
- [`docs/adr/`](docs/adr/) — durable architecture decisions.

## What works now

- Discovers the Hyperliquid perp universe and USDC-quoted spot universe dynamically.
- Persists every discovered perp with explicit pass/fail reasons, including no compatible spot.
- Intersects spot and perp by underlying token instead of hard-coding BTC/ETH, with an explicit L1-token alias map (default `UBTC:BTC`) for HyperCore remappings.
- Reads current funding, predicted funding, spot/perp mids, volume and basis.
- Pulls 7d funding history for the top candidates and computes 24h/72h EWMAs + 72h funding volatility.
- Pulls books for candidates and derives spread plus bounded two-sided USD depth.
- Ranks on **expected net carry**, not displayed funding alone.
- Archives normalized raw pulls and derived scans as local Parquet.
- Reads perp account state + spot balances with only a public account address.
- Archives actual realized user-funding records.
- Ships Lubuntu `systemd` timers.
- Runs deterministic autonomous read-only or restart-safe shadow cycles with portfolio limits,
  hysteresis, paired-fill failure recovery, hedge monitoring, exits, and replacement.
- Direct Hyperliquid execution is available through an API wallet but is default-off, persistently
  armed, capped, reconciled, and isolated from the read-only scanner client.

## RobotWealth-aligned production score

The production funding forecast is the arithmetic mean of the last 96 hourly funding observations
(four days), matching the supplied RobotWealth basis document. The older weighted ensemble remains
available only as a legacy research comparator.

The displayed expected net APR is:

`gross funding APR - round-trip cost APR - basis risk buffer APR`

where round-trip costs include a configurable fee assumption plus observed spot/perp spread and are amortized over the configured expected holding period.

This is a **scanner assumption**, not a claim that carry actually persists for that holding period. Milestone 2 will estimate persistence/realized holding periods from the archive and historical replay.

## Initial RobotWealth-aligned gates

Defaults live in `.env.example` and are intentionally configurable:

- minimum five-day average daily notional: $50m on each leg
- entry hurdle: 30% expected net APR
- exit hurdle: 10% expected net APR
- maximum absolute spot/perp basis: 300 bp
- maximum observed spread per leg: 35 bp
- assumed holding period: 168h
- assumed round-trip paired fees: 20 bp (four 5 bp leg trades; set this to your fee tier)
- fixed equal slot cap across 20 pairs; unused slots remain cash
- 3% percentage no-trade buffer; resize to the buffer edge after a breach
- only canonical USDC spot pairs are eligible
- missing funding history, predicted Hyperliquid funding, or observed top-of-book spread makes a candidate ineligible
- basis-risk buffer: 2% APR

These are operating defaults for data collection, **not frozen research parameters**.

## Lubuntu install

Copy/unzip the project onto the Lubuntu crypto machine, `cd` into it, then:

```bash
./install_lubuntu.sh
sudoedit /opt/cleancarry/.env
```

The installer uses `uv` exclusively for Python and dependency management. It installs a pinned `uv`
binary in `/usr/local/bin`, selects Python 3.12, and syncs the committed `uv.lock` into
`/opt/cleancarry/.venv`. It requires a prebuilt PyArrow wheel and reports an error if the platform
cannot use one, instead of attempting a local Arrow build. It creates `.env` before dependency
installation and preserves `.env`, archived data, state, logs, and the uv cache on reruns.
Rerun the installer after updating this repository to copy the `uv`-based service units and reload
`systemd`. A service that is already running uses its previous process until it is restarted.
`.env` is hidden from a plain `ls`; verify it with `sudo stat /opt/cleancarry/.env`. If a previous
install left it missing or zero bytes, populate it without overwriting a nonempty configuration:

```bash
sudo test -s /opt/cleancarry/.env || sudo install -o cleancarry -g cleancarry -m 600 \
  /opt/cleancarry/.env.example /opt/cleancarry/.env
```

The example leaves account addresses and the API wallet key blank because those values are specific
to your account. Fill them in with `sudoedit /opt/cleancarry/.env`.

Set at least:

```bash
HL_ACCOUNT_ADDRESS=0xYOUR_PUBLIC_HYPERLIQUID_ADDRESS
```

M1 does not need a private key.

Test the scanner:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry opportunities --all --limit 30
```

Read account state:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync cleancarry status
```

Archive realized funding:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync cleancarry funding --days 7
```

Replay one archived pair through an inclusive UTC cutoff:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync cleancarry replay BTC \
  --as-of-utc 2026-09-21T12:00:00Z
```

The replay compares the funding ensemble with a current-funding-only baseline and writes a versioned
manifest, summary, and trade-level Parquet files under `data/derived/studies/`. A study decision is
research evidence only; it does not enable live trading.

Enable unattended snapshots only after the manual commands work:

```bash
sudo systemctl enable --now cleancarry-scan.timer cleancarry-account.timer
systemctl list-timers 'cleancarry*'
```

Follow logs:

```bash
tail -f /opt/cleancarry/logs/scan.log /opt/cleancarry/logs/scan.err.log
```

Run one production decision cycle without changing paper state:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry autonomous --mode read_only --once
```

Run one persistent shadow cycle:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry autonomous --mode shadow --once
```

Inspect or control shadow state:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry paper-status
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry paper-control pause
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry paper-control resume
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry paper-control close --coin BTC
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry paper-control close-all
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry paper-control kill
```

After manual shadow cycles succeed, run unattended shadow mode directly or through systemd:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry autonomous --mode shadow
sudo systemctl enable --now cleancarry-autonomous.service
```

## Direct live execution and compounding

Live trading bypasses the still-incomplete M2/M3 evidence gates at the user's explicit direction.
Use a dedicated Hyperliquid subaccount and an authorized API wallet key—never the main wallet key.
CleanCarry queries the subaccount's `userAbstraction` mode before each live cycle. It supports
`unifiedAccount` and Standard (`disabled`); Portfolio Margin and ambiguous modes stop without
trading. In Unified Account, sizing uses marked spot balances plus open perpetual unrealized P&L,
not the legacy perps `accountValue`. The reported equity should still be checked against the
subaccount before leaving the service unattended.
In `/opt/cleancarry/.env`, set:

```env
HL_ACCOUNT_ADDRESS=0xMAIN_ACCOUNT
HL_SUBACCOUNT_ADDRESS=0xDEDICATED_CLEANCARRY_SUBACCOUNT
HL_API_WALLET_PRIVATE_KEY=0xAPI_WALLET_KEY
LIVE_TRADING_ENABLED=true
```

`CC_LIVE_CAPITAL_FRACTION=0.25` is a CleanCarry safety default, not a RobotWealth sizing rule;
set the nominal allocation deliberately for the account. Review all `CC_LIVE_*` caps, then arm the
persistent state and run exactly one cycle first:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry live-control arm --confirm I_ACCEPT_LIVE_TRADING
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync cleancarry live --once
```

The live target is recomputed from observed account equity each cycle. Profits increase and losses
decrease subsequent target sizes, but per-order and total-deployment caps always win. After verifying
the account and `state/live_state.json`, continuous operation can be started explicitly:

```bash
sudo systemctl enable --now cleancarry-live.service
```

With this updated version, request a stop before the next new paired action:

```bash
sudo -u cleancarry env HOME=/opt/cleancarry UV_CACHE_DIR=/opt/cleancarry/.cache/uv \
  /usr/local/bin/uv run --project /opt/cleancarry --frozen --no-sync \
  cleancarry live-control safe
systemctl is-active cleancarry-live.service
```

The live control creates a persistent stop marker that the running process checks before new
paired actions. It does not interrupt an already-started pair's required hedge/recovery, and
the process may take until its next cycle to exit. Once it has exited, `sudo systemctl stop
cleancarry-live.service` ensures systemd marks it stopped. Stopping the service never closes an
open position. Inspect spot and perp exposure on Hyperliquid before stopping or restarting. Older
versions do not honor the stop marker while already running: stop that old service before deploying
this fix. To deploy an updated version, stop the service, rerun the installer from the updated
checkout, verify account balances and state, then explicitly re-arm and restart.

## Data layout

```text
data/raw/
  perp_contexts_*.parquet
  spot_contexts_*.parquet
  predicted_fundings_*.parquet
  funding_history_<COIN>.parquet
  account_summary_*.parquet
  perp_positions_*.parquet
  spot_balances_*.parquet
  user_funding.parquet

data/derived/
  carry_markets_*.parquet
  opportunities_*.parquet
  universe_funnel_*.parquet
  strategy_cycles_*.parquet
  studies/<STUDY_ID>/
    manifest.json
    summary.json
    trades_ensemble.parquet
    trades_current_only.parquet
```

Persistent shadow control state and its append-only audit trail live outside Git:

```text
state/paper_state.json
state/paper_ledger.jsonl
```

## Safety boundary

No private key belongs in Git. Live signing accepts only the API-wallet key from the protected
runtime environment. `LIVE_TRADING_ENABLED` defaults false and is insufficient by itself; persistent
arming, account binding, hard caps, paired-leg handling, and reconciliation must also pass.

## Next milestone: historical carry simulator

The canonical scope and promotion gate live in [`ROADMAP.md`](ROADMAP.md). The simulator will use
the archive to answer:

1. How persistent is positive funding after a candidate passes the scanner?
2. What holding horizon actually maximizes realized net carry after costs?
3. How often does basis movement overwhelm funding income?
4. How much turnover does 30%/10% entry/exit hysteresis create?
5. Does the RobotWealth 96-hour mean beat the legacy ensemble and current funding alone?
6. What liquidity/basis/funding-volatility filters materially improve realized carry?

Only evidence that passes that gate advances to M3 paper/reconciliation and, later, a separately
approved M4 tiny live subaccount.
