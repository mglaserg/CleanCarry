# CleanCarry project status

**Canonical status date:** 2026-09-21  
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

- Dynamic Hyperliquid perpetual and canonical USDC spot discovery.
- Explicit HyperCore spot/perpetual alias configuration, defaulting to `UBTC:BTC`.
- Current and predicted funding, 24h/72h EWMAs, 72h funding volatility, basis, volume, and
  top-of-book spread inputs.
- Transparent funding ensemble and configurable cost/risk gates.
- Conservative eligibility behavior when required history, prediction, or spread data is missing.

### Data and account observation

- Timestamped local Parquet snapshots for normalized market, opportunity, account, position, and
  balance records.
- Deduplicated time-series archives for funding history and realized user funding.
- Read-only account inspection using a public address.
- Generated data, logs, state, `.env`, and credentials excluded from Git.

### Operations and safety

- Typer CLI commands: `opportunities`, `status`, `funding`, `replay`, and the hard-stop `live`
  command.
- Lubuntu installation script and hardened `systemd` oneshot/timer units.
- No private-key configuration, signing dependency, exchange client, or order-sending code.

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

On 2026-09-21 using Python 3.12.3:

- `pytest`: **10 passed**;
- `ruff check .`: **passed**;
- study persistence and ensemble/current-only comparison were exercised with synthetic Parquet
  archives, including an excluded future observation;
- root and `replay` CLI help smoke checks passed using the declared Typer/Rich dependencies in an
  isolated temporary dependency directory.

No live Hyperliquid smoke test or Lubuntu/systemd install test was run during this documentation
baseline. Do not interpret unit-test success as upstream or operational verification.

## What is incomplete

- The repository data directories currently contain no real archive on which to run the new study.
- No portfolio-wide archived-data coverage/cadence/gap audit exists; validation is currently scoped
  to files contributing observations for the selected coin.
- Snapshots from one scan are not tied together by a run ID or manifest and are not atomic.
- Stored records have no explicit schema version or migration system.
- Funding ensemble weights and risk buffers are assumptions, not validated parameters.
- The spread/fee model is intentionally simple; market impact and partial execution are not modeled.
- API requests have timeouts but no explicit retry/backoff or rate-limit policy.
- Tests cover scanner math and the first replay/accounting slice, but not archive write behavior,
  account normalization, CLI rendering, broad malformed payload cases, or exact venue funding
  settlement timing.
- No paper ledger, target sizing, leg state machine, or restart reconciliation exists.
- Live trading is intentionally impossible.

## Current objective

Run and harden the M2 slice on real archived observations:

1. collect or locate sufficient `carry_markets` history for at least one eligible coin;
2. add portfolio-wide coverage, cadence, gap, and schema auditing before study execution;
3. validate interval-funding assumptions against actual settlement timestamps/payments;
4. add holding-period, turnover, mark-to-market drawdown, and tail diagnostics;
5. add at least one simpler baseline and sensitivity analysis for costs/hurdles;
6. execute the registered study and retain its first real `GO`, `KILL`, or `INSUFFICIENT_DATA`
   result without changing the rule after seeing the outcome.

## Recommended next files

Continue within the separate research namespace rather than expanding `scanner.py` into a
backtester:

```text
src/cleancarry/research/     implemented single-pair slice
  replay.py                  signal preparation and chronological replay
  accounting.py              paired-leg P&L identity
  studies.py                 archive loading and reproducible artifacts
tests/
  test_replay.py             as-of, replay, and manifest coverage
  test_accounting.py         attribution identity coverage
```

The study artifact contract is recorded in ADR 0003. Extend it compatibly or supersede the ADR when
requirements force a breaking semantic change.

## Handoff checklist

A fresh session should:

1. read `AGENTS.md`, this file, `ROADMAP.md`, and `ARCHITECTURE.md`;
2. run tests and lint before changing behavior;
3. keep M2 read-only and historical;
4. run and harden the replay against real archived observations;
5. update this file, `ROADMAP.md`, and `CHANGELOG.md` with the result.
