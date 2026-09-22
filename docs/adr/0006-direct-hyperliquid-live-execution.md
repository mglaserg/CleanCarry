# ADR 0006 — Direct Hyperliquid live execution

**Status:** Accepted  
**Date:** 2026-09-22  
**Supersedes:** ADR 0001's prohibition on introducing execution code before the M3 evidence gate

## Context

The user explicitly directed CleanCarry to trade directly on Hyperliquid without waiting for the
future Conductor integration. Historical and sustained paper evidence remain incomplete, so live
operation carries materially greater model, execution, and reconciliation risk than the original
milestone order allowed.

## Decision

CleanCarry may contain and run a direct Hyperliquid execution boundary subject to all of these
controls:

- signing uses a Hyperliquid API wallet; operators must not provide the main withdrawal-capable key;
- `LIVE_TRADING_ENABLED=true` and persistent `live-control arm` state are both required;
- arming binds the state file to the configured main account or subaccount address;
- per-order, total-deployment, capital-fraction, slippage, position-count, liquidity, and strategy
  limits apply before an order is sent;
- every paired action is persisted before its first leg and uses deterministic client order IDs;
- spot is acquired first and the equal-quantity perpetual hedge is submitted immediately; a failed
  hedge triggers a spot flatten attempt and `SAFE_MODE`;
- restart with an unresolved action or any managed-position/account mismatch enters `SAFE_MODE`;
- the strategy performs at most one entry, exit, increase, or reduction per cycle;
- live target capital is recomputed from observed account equity every cycle, so gains and losses
  compound into later target sizes only within the configured absolute caps;
- the live service never automatically restarts after an execution failure.

ADR 0001 remains valid for its evidence warning and milestone history, but its absolute statement
that no execution code may exist is superseded by this explicit user-authorized decision.

## Consequences

- Direct live trading is technically possible but remains default-off after install and restart.
- A dedicated subaccount is strongly recommended because reconciliation deliberately fails closed
  when managed quantities do not match the observed account.
- The current executor uses bounded-slippage IOC orders. The RobotWealth document's maker-first
  quoting sequence remains future execution-quality work.
- A code-complete live path is not evidence that the strategy is profitable or that the M2/M3 gates
  passed. Operators assume the additional risk of running before those gates are complete.
- Continuous compounding is bounded, not unlimited: equity changes resize targets, while the
  absolute order and deployment caps remain authoritative.
