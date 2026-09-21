# CleanCarry agent guide

This file is the operating contract for coding agents working in this repository.

## Read this first

Before making a meaningful change, read these files in order:

1. `PROJECT_STATUS.md` — what is true now, what is incomplete, and the immediate objective.
2. `ROADMAP.md` — milestone order and promotion gates.
3. `ARCHITECTURE.md` — system boundaries, data flow, units, and invariants.
4. `CHANGELOG.md` — recent behavior and architecture changes.
5. Relevant records under `docs/adr/` before changing a durable decision.

The repository is the durable source of truth. When chat history and repository documentation
disagree, call out the mismatch and reconcile the documentation with the accepted change.

## Product mission

CleanCarry discovers and studies same-venue, delta-neutral Hyperliquid carry:

```text
long canonical USDC spot + short equal-dollar perpetual
```

The project should earn the right to trade through archived evidence, historical replay, and
paper reconciliation. It is currently a read-only research and account-observation tool.

## Non-negotiable boundaries

- Milestones 1 and 2 are read-only. They must not accept private keys, sign payloads, or submit,
  amend, or cancel orders.
- `cleancarry live` remains an explicit hard stop until the live-execution milestone is approved
  and implemented with its own architecture decision record.
- Venue access in the current product goes through Hyperliquid's public `/info` API only.
- Eligible carry pairs use the same venue, a canonical USDC spot market, and an explicitly mapped
  matching perpetual. Do not guess symbol aliases.
- Missing predicted funding, funding history, or observed book spread fails eligibility closed.
- Preserve units: funding rates are hourly decimal rates; APR is simple annualization over 8,760
  hours; prices and notionals are USD/USDC; spreads and basis are basis points.
- Raw observations and normalized archives are evidence. Never silently overwrite or relabel them.
- Historical research must use information available at the simulated decision time. Future data
  and non-as-of joins are forbidden.
- A high displayed APR is a candidate for research, not proof of a tradeable strategy.

If a request conflicts with one of these boundaries, do not work around it. Update or supersede
the relevant ADR in the same change and make the safety consequences explicit.

## Design principles

- Prefer inspectable calculations and explicit assumptions over opaque models.
- Falsify cheaply before adding infrastructure or execution complexity.
- Separate source retrieval, normalization, scoring, archival, portfolio state, and execution.
- Keep the universe discovered from venue metadata; hard-coded one-off coin lists are not the
  product.
- Treat transaction costs, basis movement, funding reversals, liquidity, and leg risk as first-class
  risks before claiming profitability.
- Prefer local Parquet and small Python modules until measured workload proves they are inadequate.
- Do not add databases, queues, distributed schedulers, dashboards, or services without a concrete
  requirement and an ADR when the choice is durable.

## How to choose work

Unless the user explicitly reprioritizes:

1. Take the highest-priority unblocked item in `PROJECT_STATUS.md` and `ROADMAP.md`.
2. Complete one end-to-end capability slice rather than scattering partial scaffolding.
3. Preserve the milestone gates: archive -> replay -> paper/reconcile -> tiny live.
4. For research changes, state the hypothesis, decision time, costs, evaluation metric, and
   GO/KILL criterion before promoting a result into portfolio logic.

## Repository map

```text
src/cleancarry/
  cli.py             Typer commands and presentation
  config.py          environment-backed settings
  hyperliquid.py     read-only public API adapter
  scanner.py         universe intersection, features, eligibility, ranking
  models.py          normalized domain records
  archive.py         Parquet snapshots and deduplicated time series
  account.py         read-only account normalization
  research/          as-of replay, P&L attribution, and study persistence
tests/                deterministic unit tests
systemd/              Lubuntu collection services and timers
data/raw/              ignored runtime source archives
data/derived/          ignored scored/derived archives
state/                 ignored future persistent control state
logs/                  ignored runtime logs
docs/adr/              durable architecture decisions
```

Generated Parquet, logs, local state, `.env`, and secrets do not belong in Git.

## Definition of done

A meaningful change should include, as applicable:

- implementation with explicit types and failure behavior;
- deterministic tests for new logic and regressions;
- no-lookahead coverage for replay/research paths;
- configuration and `.env.example` updates for new operator settings;
- documentation and project-memory updates;
- migration notes when a stored schema or interpretation changes;
- verification commands actually run, with failures reported honestly.

For execution-related work, “done” additionally requires the milestone gate in `ROADMAP.md`, an
accepted ADR, dry-run/paper evidence, idempotency, restart reconciliation, stale-data rejection,
paired-leg handling, exposure limits, and a persistent arming mechanism. A function that can send
an order is not, by itself, a completed execution feature.

## Required project-memory updates

Every meaningful feature change should update repository memory in the same commit:

- `PROJECT_STATUS.md` when current truth, verification, gaps, or next work changed;
- `ROADMAP.md` when milestone scope or status changed;
- `CHANGELOG.md` with a concise entry under `Unreleased`;
- `ARCHITECTURE.md` and/or `docs/adr/` when a durable design choice changed.

Tiny typo-only edits do not require every file. Do not create competing status or roadmap copies.

## Verification

From the repository root:

```bash
python -m pytest
python -m ruff check .
```

When validating packaging or CLI wiring:

```bash
python -m pip install -e ".[dev]"
cleancarry --help
```

Live API smoke tests are optional and network-dependent. Never make them a unit-test prerequisite,
and never claim they passed unless they were run. Never weaken assertions merely to accommodate a
transient upstream response.

## Handoff standard

Before handing work off, leave `PROJECT_STATUS.md` accurate enough for a fresh contributor to answer:

- What works now?
- What was actually verified, and when?
- What remains research-only or incomplete?
- What is the next highest-priority slice?
- Which safety boundary or ADR constrains it?
