# ADR 0003 — Reproducible replay study contract

**Status:** Accepted  
**Date:** 2026-09-21

## Context

M2 must test whether scanner candidates survive costs without allowing future observations, mutable
configuration, or post-hoc decision rules to change the conclusion. The M1 archive consists of
timestamped normalized `carry_markets` snapshots rather than an atomic event stream.

## Decision

The first replay contract is deliberately narrow:

- one coin per study;
- `observed_at_utc` is the decision clock;
- an optional inclusive UTC cutoff is applied before signal preparation;
- duplicate coin/timestamp observations and missing required columns fail the study;
- both the ensemble and current-funding-only baseline use the same filtered price path, notional,
  fees, and spread convention;
- funding for an observed interval uses only its starting rate and perpetual price;
- positions enter and exit using configurable APR hysteresis and close at the end of available data;
- every study declares its hypothesis and GO/KILL rule before reporting results;
- an insufficient trade count produces `INSUFFICIENT_DATA`, not a directional conclusion.

A completed schema-v1 study directory contains trade-level Parquet for each model, `summary.json`,
and a manifest written last. The manifest records configuration, limitations, package/source-tree
identity, input file hashes and coverage, output hashes, model summaries, and the decision.

## Consequences

- A study can be reproduced and audited without relying on chat history or mutable defaults.
- Future rows cannot influence signal preparation before the declared cutoff.
- The initial funding and spread treatment is transparent but approximate; exact settlement timing,
  market impact, and intratrade drawdown remain required validation work.
- Schema-breaking changes require a new schema version and migration/reader policy, or a superseding
  ADR when the meaning changes materially.

