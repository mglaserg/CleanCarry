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
- ADR 0005 and a RobotWealth-aligned production strategy: 96-hour arithmetic funding mean,
  five-day $50M average-volume universe, 30%/10% net-APR hysteresis, equal 20-slot nominal sizing,
  20% modeled perp collateral, and a percentage buffer that trades to its edge.
- Daily spot/perp candle retrieval and archives for the five-day dollar-volume gate.
- Direct Hyperliquid API-wallet execution with persistent arming, deterministic client order IDs,
  paired-leg recovery, fail-closed reconciliation, hard live limits, and equity-based compounding.
- A non-restarting systemd live service and ADR 0006 documenting the user-authorized execution
  boundary and its pre-evidence risks.

### Changed

- Detect Hyperliquid account-abstraction mode in each live cycle; value Unified Account from spot
  balances and open perp P&L without adding the misleading legacy perp account value, and fail
  closed for unknown or Portfolio Margin modes.
- Persist live stop requests outside the cycle-state file and check them before new paired actions,
  so an active service cannot silently overwrite an operator's stop request.
- Refuse Lubuntu upgrades while the live service is active, avoiding a mixed old-process/new-files
  deployment.
- Route all four Lubuntu `systemd` services through the locked `uv run` environment and replace
  pip-based contributor verification commands with `uv` equivalents.
- Verify at install completion that the `cleancarry` user can read `.env` and print its path,
  ownership, and permissions without revealing configuration values.
- Initialize a pre-existing zero-byte `.env` from the example; preserve nonempty operator
  configuration on reinstall.
- Create `/opt/cleancarry/.env` before the `uv` dependency sync and fail explicitly if the
  configuration file is missing; preserve existing configuration on reinstall.
- Pin the Lubuntu environment to Python 3.12 and require a binary PyArrow wheel to avoid an
  unsupported source build during installation.
- Replaced the Lubuntu `python3-venv`/`pip` bootstrap with a pinned system-wide `uv` install and a
  locked production sync; reinstalls now preserve operator configuration and runtime evidence.
- Cleared the existing Ruff baseline and narrowed per-market upstream exception handling to expected
  HTTP/decoding failures.
- Made snapshot and time-series Parquet writes atomic and collision-resistant at microsecond
  filename resolution.
- Corrected Hyperliquid spot eligibility to accept documented `@index` USDC markets; the
  market-level `isCanonical` flag describes naming and previously excluded valid HYPE and other
  exact-name spot/perpetual pairs.
- Matched spot contexts by their returned `coin` identifier instead of list position; current API
  context arrays include additional indexed entries and are not positionally aligned to metadata.

## 0.1.0 — 2026-09-10

- Added the read-only Hyperliquid spot/perpetual carry scanner.
- Added dynamic universe intersection, funding features, basis/liquidity/spread gates, and
  cost-aware ranking.
- Added local Parquet archives, public-address account snapshots, realized-funding collection, and
  Lubuntu `systemd` timers.
- Added an explicit live-trading hard stop and initial deterministic scanner tests.
