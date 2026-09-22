# ADR 0005 — RobotWealth basis strategy semantics

**Status:** Accepted  
**Date:** 2026-09-22

## Context

The supplied “Crypto Perpetual Futures Basis” document is the strategy source for CleanCarry. The
initial scanner used a locally invented weighted funding ensemble and looser thresholds, which was
not a faithful implementation of that source.

## Decision

The production decision path uses these source-grounded semantics:

- same-venue long spot and equal-notional short perpetual;
- five-day average daily dollar volume, with a $50 million default minimum on both legs;
- arithmetic mean of the last 96 hourly funding observations as the funding forecast;
- 30% expected-net-APR entry and 10% expected-net-APR exit hysteresis;
- a nominal capital allocation with 20% modeled perpetual collateral;
- 20 equal portfolio slots by default, each capped at
  `capital / (slots * (1 + perp_margin_fraction))`;
- empty slots remain cash instead of concentrating into fewer names;
- a 3% percentage no-trade region by default; after a target deviation breaches half the buffer,
  resize both legs only to the corresponding buffer edge;
- no volatility targeting or automatic capital scaling.

CleanCarry deliberately applies the 30%/10% hurdle to expected **net** APR after modeled costs and
basis-risk buffer, which is more conservative than the document's funding-only screening example.
It also retains spread, depth, basis, staleness, and explicit spot-mapping gates required by the
product safety contract.

The previous current/predicted/EWMA ensemble remains available as `legacy_ensemble` for historical
comparison. It is not the default production forecast.

## Consequences

- Production ranking and allocation now correspond to the supplied strategy rather than a locally
  invented signal.
- Daily candles and funding history become required decision evidence and are archived.
- Existing archives without the new fields remain readable only through the legacy replay path.
- The document's quote-less-liquid-leg, then immediately hedge-more-liquid-leg execution procedure
  is not yet a live capability. Shadow execution still tests paired state and recovery, while live
  trading remains prohibited by ADR 0001 until its evidence gate is satisfied.
