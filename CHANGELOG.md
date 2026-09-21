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

### Changed

- Cleared the existing Ruff baseline and narrowed per-market upstream exception handling to expected
  HTTP/decoding failures.

## 0.1.0 — 2026-09-10

- Added the read-only Hyperliquid spot/perpetual carry scanner.
- Added dynamic universe intersection, funding features, basis/liquidity/spread gates, and
  cost-aware ranking.
- Added local Parquet archives, public-address account snapshots, realized-funding collection, and
  Lubuntu `systemd` timers.
- Added an explicit live-trading hard stop and initial deterministic scanner tests.
