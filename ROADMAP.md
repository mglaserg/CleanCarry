# CleanCarry roadmap

This is the canonical roadmap. Milestones are evidence and safety gates, not dates or collections
of screens. A milestone advances only when its gate is demonstrably satisfied.

Status vocabulary:

- **DONE** — implemented and verified for the stated scope.
- **ACTIVE** — current highest-priority milestone.
- **QUEUED** — planned after prerequisites are met.
- **PARKED** — intentionally deferred.

## M1 — Read-only scanner and archive — DONE

- dynamic Hyperliquid perpetual/canonical-USDC-spot intersection;
- explicit spot-to-perpetual aliases;
- current, predicted, EWMA24, and EWMA72 funding inputs;
- basis, liquidity, and observed spread gates;
- cost-aware expected-net-APR ranking;
- timestamped Parquet market/account snapshots;
- deduplicated funding-history and realized-funding archives;
- read-only public-address account state;
- Lubuntu `systemd` collection timers;
- hard-stop `live` command with no execution code;
- deterministic scanner tests.

**Gate passed:** the repository can identify and archive inspectable candidates without credentials
that can trade. Operational burn-in and data-quality monitoring continue as cross-cutting work.

## M2 — Historical carry simulator — ACTIVE

### Scope

- inventory archived coverage, cadence, gaps, duplicates, and schema consistency — **PARTIAL:**
  selected-coin schema, file hashes, coverage, and duplicate checks are implemented;
- define an as-of decision clock and replay contract with no future leakage — **DONE for the
  single-pair slice**;
- reconstruct entry/exit decisions using configurable APR hysteresis — **DONE for one pair**;
- model both legs, interval funding, basis P&L, fees, and spread/slippage assumptions — **DONE for
  the initial transparent model; exact settlement timing and market impact remain**;
- compare the current ensemble with current-funding-only and simpler baselines — **DONE for the
  ensemble/current-only comparison; additional baselines remain**;
- measure holding-period distribution, turnover, drawdown, and tail outcomes;
- segment results by liquidity, basis, funding volatility, and data completeness;
- save reproducible study artifacts with configuration, data identity, code version, and results —
  **DONE for schema version 1**;
- add synthetic fixtures that catch lookahead and accounting errors — **DONE for the initial slice**.

### Gate

On a versioned archived dataset, reproduce a study from one command and explain total net P&L as:

```text
funding received + spot P&L + perpetual P&L - fees - modeled slippage
```

The study must pass no-lookahead tests and make a documented GO/KILL decision on whether the signal
earns paper trading. Attractive headline APR alone does not pass the gate.

## M3 — Paper portfolio and reconciliation — QUEUED

- persistent intent/order/fill/position ledger — **PARTIAL:** atomic state and append-only shadow
  events exist; double-entry cash accounting, locking, and crash-injection coverage remain;
- target sizing with max notional, single-name exposure, liquidity, capital, collateral, and modeled
  margin limits — **DONE for shadow selection**;
- paired-leg state machine with partial-fill and timeout handling in simulation — **PARTIAL:**
  success, rejection, partial hedged fill, unmatched-leg recovery, safe mode, and idempotency are
  covered; elapsed timeout scheduling and venue-calibrated fills remain;
- restart-safe reconciliation between intended and observed positions — **PARTIAL:** persisted shadow
  restart and explicit mismatch detection exist; public-account integration remains;
- margin-utilization and temporary-delta limits — **PARTIAL:** allocation and hedge-error controls
  exist; temporary-delta duration and cash-ledger enforcement remain;
- stale-data and degraded-upstream circuit breakers — **PARTIAL:** per-opportunity staleness and safe
  mode exist; retry/backoff and prolonged-upstream policy remain;
- daily P&L attribution and funding reconciliation — **NOT STARTED in autonomous cycles**;
- operator runbook, alerts, and explicit paper-only mode — **PARTIAL:** CLI controls/status and a
  shadow systemd service exist; alert delivery and burn-in runbook remain.

### Gate

Run a sustained paper/shadow period with no unexplained position or cash drift, demonstrate recovery
from partial-leg and restart scenarios, and document whether observed costs remain inside the M2
assumptions. Any unexplained reconciliation break resets the evidence window.

## M4 — Tiny live subaccount — QUEUED

### Prerequisites

- explicit user approval to add execution capability;
- an accepted execution/safety ADR;
- isolated Hyperliquid subaccount and signer boundary;
- M3 gate passed with retained evidence.

### Scope

- persistent arming state that defaults off after install and recovery;
- account-identity and environment checks;
- idempotent client order IDs and restart reconciliation;
- post-only/limit policy with explicit fallback behavior;
- maximum temporary delta, order notional, strategy notional, name exposure, and margin utilization;
- kill switch and cancel/flatten runbook with clearly separated authority;
- immutable decision and execution audit log;
- tiny capital limits that cannot be raised accidentally by a research config change.

### Gate

Complete a manually supervised tiny-capital canary with every decision, order, fill, funding payment,
and reconciliation event attributable. Promotion requires an explicit human decision; the software
must never self-promote capital limits.

## M5 — Operational hardening — QUEUED

- data freshness/coverage health checks and actionable alerts;
- retry/backoff and rate-limit behavior with bounded failure modes;
- versioned stored schemas and migrations;
- backup/restore drills for research artifacts and control state;
- release/version process and CI across supported Python versions;
- operator dashboard only after the underlying state and alerts are trustworthy.

### Gate

Demonstrate installation, upgrade, restart, degraded-data handling, and state recovery from a written
runbook without creating an untracked position or corrupting the research record.

## Parked until evidence earns them

- additional venues and cross-venue carry;
- leverage optimization;
- machine-learning forecasts beyond transparent baselines;
- distributed storage, queues, or orchestration;
- high-frequency execution;
- autonomous capital scaling.
