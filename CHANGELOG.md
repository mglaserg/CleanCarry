# Changelog

All meaningful CleanCarry changes should be recorded here. Keep entries concise and describe
behavior, safety, and architecture changes rather than listing every edited file.

## Unreleased

### Added

- Root agent operating guide with repository map, safety boundaries, definition of done, and
  project-memory rules.
- Canonical architecture, roadmap, current-status, and changelog documents.
- Architecture Decision Record index and initial decisions for read-only milestone promotion and
  local Parquet evidence storage.
- Documentation navigation from the README.
- Read-only single-pair historical replay with strict UTC/as-of filtering and configurable APR
  hysteresis.
- Explicit spot, perpetual, funding, fee, spread/slippage, and net P&L attribution.
- Reproducible schema-v1 study artifacts with data/source/output hashes and an ensemble-versus-
  current-only GO/KILL rule.
- `cleancarry replay` command and synthetic no-lookahead/accounting regression coverage.
- Full-perpetual-universe funnel records with explicit rejection codes, open-interest and bounded
  two-sided depth gates, and per-opportunity capacity estimates.
- A shared read-only/shadow autonomous strategy pipeline with deterministic ranking, capital-aware
  sizing, hysteresis, hedge buffers, exits, and same-cycle replacement.
- Atomic schema-versioned paper state, append-only audit events, idempotent paired-fill simulation,
  failed-leg recovery, safe mode, restart reconciliation, and operator controls.
- Autonomous lifecycle tests covering selection, limits, hold/exit hysteresis, partial and rejected
  legs, recovery, duplicate actions, stale data, hedge correction, redeployment, and restart.
- Hardened autonomous shadow systemd service and ADR 0004 defining the future Conductor boundary.

### Changed

- Cleared the existing Ruff baseline and narrowed per-market upstream exception handling to expected
  HTTP/decoding failures.
- Made snapshot and time-series Parquet writes atomic and collision-resistant at microsecond
  filename resolution.

## 0.1.0 — 2026-09-10

- Added the read-only Hyperliquid spot/perpetual carry scanner.
- Added dynamic universe intersection, funding features, basis/liquidity/spread gates, and
  cost-aware ranking.
- Added local Parquet archives, public-address account snapshots, realized-funding collection, and
  Lubuntu `systemd` timers.
- Added an explicit live-trading hard stop and initial deterministic scanner tests.
