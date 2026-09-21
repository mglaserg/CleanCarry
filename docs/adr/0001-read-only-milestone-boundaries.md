# ADR 0001 — Read-only milestones precede execution

**Status:** Accepted  
**Date:** 2026-09-21

## Context

An apparent spot/perpetual funding spread is not sufficient evidence of tradeability. Funding can
reverse, basis and liquidity can move, costs can dominate short holding periods, and two-leg
execution creates temporary directional exposure. Adding a private key or order call early would
expand the blast radius before the research and reconciliation assumptions are tested.

## Decision

CleanCarry progresses through explicit capability gates:

```text
read-only archive -> historical replay -> paper/reconciliation -> tiny live subaccount
```

Milestones 1 and 2 do not accept private keys or contain signing/order code. Milestone 3 models and
reconciles portfolio state without live authority. Live execution may be introduced only after the
M3 gate, explicit user approval, and a new accepted ADR specifying the signer boundary, arming,
limits, paired-leg state machine, idempotency, recovery, and audit trail.

`LIVE_TRADING_ENABLED` is not execution authority. In the current system it is a visible future
latch and `cleancarry live` always exits without sending an order.

## Consequences

- Research and data collection can run with only public information and a public account address.
- A future execution implementation is a deliberate architecture change, not an incremental edit
  to the scanner client.
- Roadmap promotion depends on retained evidence and reconciliation quality rather than feature
  presence alone.
- Development is slower than adding direct order calls, but failures remain observable and bounded
  while the strategy assumptions are still being tested.

