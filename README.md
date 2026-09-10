# CleanCarry v0.1 — Milestone 1

**Lubuntu + Hyperliquid. Read-only by construction.**

CleanCarry looks for same-venue delta-neutral carry candidates:

`LONG Hyperliquid spot + SHORT equal-dollar Hyperliquid perp`

The first milestone deliberately does **not** send orders. It establishes the market-data archive, spot/perp universe intersection, funding forecast baseline, cost-aware opportunity score, account-state reader, realized-funding archive, tests, and systemd scheduling.

## What works now

- Discovers the Hyperliquid perp universe and USDC-quoted spot universe dynamically.
- Intersects spot and perp by underlying token instead of hard-coding BTC/ETH, with an explicit L1-token alias map (default `UBTC:BTC`) for HyperCore remappings.
- Reads current funding, predicted funding, spot/perp mids, volume and basis.
- Pulls 7d funding history for the top candidates and computes 24h/72h EWMAs + 72h funding volatility.
- Pulls top-of-book spreads for the top candidates.
- Ranks on **expected net carry**, not displayed funding alone.
- Archives normalized raw pulls and derived scans as local Parquet.
- Reads perp account state + spot balances with only a public account address.
- Archives actual realized user-funding records.
- Ships Lubuntu `systemd` timers.
- `cleancarry live` is a hard stop: there is no order-signing/sending code in M1.

## Baseline score

The funding estimate is intentionally simple and inspectable:

`25% current + 35% predicted + 25% EWMA24 + 15% EWMA72`

Weights renormalize when a component is unavailable.

The displayed expected net APR is:

`gross funding APR - round-trip cost APR - basis risk buffer APR`

where round-trip costs include a configurable fee assumption plus observed spot/perp spread and are amortized over the configured expected holding period.

This is a **scanner assumption**, not a claim that carry actually persists for that holding period. Milestone 2 will estimate persistence/realized holding periods from the archive and historical replay.

## Initial gates

Defaults live in `.env.example` and are intentionally configurable:

- minimum spot 24h notional: $250k
- minimum perp 24h notional: $1m
- entry hurdle: 10% expected net APR
- future exit hurdle: 5% expected net APR
- maximum absolute spot/perp basis: 300 bp
- maximum observed spread per leg: 35 bp
- assumed holding period: 72h
- assumed round-trip fees: 12 bp (set this to your actual fee tier before trading)
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

Set at least:

```bash
HL_ACCOUNT_ADDRESS=0xYOUR_PUBLIC_HYPERLIQUID_ADDRESS
```

M1 does not need a private key.

Test the scanner:

```bash
sudo -u cleancarry /opt/cleancarry/.venv/bin/cleancarry opportunities --all --limit 30
```

Read account state:

```bash
sudo -u cleancarry /opt/cleancarry/.venv/bin/cleancarry status
```

Archive realized funding:

```bash
sudo -u cleancarry /opt/cleancarry/.venv/bin/cleancarry funding --days 7
```

Enable unattended snapshots only after the manual commands work:

```bash
sudo systemctl enable --now cleancarry-scan.timer cleancarry-account.timer
systemctl list-timers 'cleancarry*'
```

Follow logs:

```bash
tail -f /opt/cleancarry/logs/scan.log /opt/cleancarry/logs/scan.err.log
```

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
```

## Safety boundary

No private key belongs in M1. `LIVE_TRADING_ENABLED` defaults to false, and even setting it true cannot cause a trade because order code is intentionally absent.

Before Milestone 4 live trading, we will add separate signing/execution code behind guards for account identity, staleness, paired-leg completion, max temporary delta, margin utilization, max notional, max single-name exposure, existing-position reconciliation, and a persistent live arming switch.

## Next milestone: historical carry simulator

Milestone 2 will build a replay from archived/historical funding + spot/perp prices and answer the questions that matter before paper trading:

1. How persistent is positive funding after a candidate passes the scanner?
2. What holding horizon actually maximizes realized net carry after costs?
3. How often does basis movement overwhelm funding income?
4. How much turnover does 10%/5% entry/exit hysteresis create?
5. Does the simple funding ensemble beat current funding alone?
6. What liquidity/basis/funding-volatility filters materially improve realized carry?

Then: M3 paper portfolio + hedge/reconciliation ledger. M4 tiny live subaccount.
