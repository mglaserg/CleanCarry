# ADR 0004 — Autonomous shadow strategy boundary

**Status:** Accepted  
**Date:** 2026-09-21

## Context

CleanCarry needs an independently testable strategy loop before any live authority is considered.
The loop must discover the venue universe, explain exclusions, select and size a portfolio, model
paired execution failures, monitor hedge state, exit deteriorating positions, redeploy capital, and
survive restart. ADR 0001 still prohibits signing and order transmission until the historical and
paper evidence gates pass.

## Decision

Add one deterministic strategy boundary with two permitted modes:

- `read_only` evaluates the production decision pipeline without changing portfolio state;
- `shadow` runs the same decisions through a persistent simulated paired-leg executor.

The pipeline is:

```text
venue perps -> canonical spot mapping -> market/history/book filters
            -> expected-net-carry ranking -> capital-constrained selection
            -> paired shadow execution -> hedge monitoring
            -> hysteresis exit -> same-cycle capital redeployment
```

Every discovered perpetual receives a universe record. Contracts without a canonical USDC hedge
receive `NO_SPOT_HEDGE`; mapped contracts receive explicit filter codes. Only filtered survivors are
ranked. Selection is deterministic by expected net APR and coin name, then constrained by position,
single-name, total deployment, liquidity-capacity, strategy-capital, and modeled margin limits.

Shadow state is an atomically replaced schema-versioned JSON file. Decision, order/fill simulation,
recovery, reconciliation, and operator-control events append to a JSON-lines audit ledger. Action IDs
make simulated execution idempotent. A one-leg or materially mismatched entry is flattened in the
simulation; failed recovery creates explicit unresolved exposure and `SAFE_MODE`. Restart loads the
persisted state before selection, so existing positions are not entered again.

The strategy owns universe requirements, forecasting inputs, eligibility, ranking, allocation,
hysteresis, desired exposures, and strategy-specific limits. A future Conductor integration may own
scheduling, supervision, invocation, and approved execution routing, but it must call this boundary
rather than reimplement strategy decisions.

## Consequences

- The autonomous lifecycle can be exercised and burned in without credentials or order authority.
- Read-only and shadow decisions cannot drift into separate strategy implementations.
- The paper ledger is local and single-process; locking, crash injection between every filesystem
  write, daily P&L/funding reconciliation, and sustained operational evidence remain M3 work.
- This ADR does not promote M2 or M3 and does not approve M4. `cleancarry live` remains a hard stop.
- A future live adapter requires the M3 gate, explicit approval, and a separate accepted execution
  ADR covering signer isolation, venue order semantics, arming, recovery, and immutable audit.
