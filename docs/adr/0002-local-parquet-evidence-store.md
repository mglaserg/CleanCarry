# ADR 0002 — Use local Parquet as the initial evidence store

**Status:** Accepted  
**Date:** 2026-09-21

## Context

CleanCarry runs on a single Lubuntu research machine and currently collects modest hourly market
snapshots plus account observations. The immediate need is inspectable columnar history for replay,
not shared transactional infrastructure.

## Decision

Use local Parquet files for the initial research store:

- timestamped snapshot types append by creating a new UTC-named file;
- funding history and realized user funding use deduplicated time-series files;
- normalized source observations live under `data/raw`;
- scores and other computations live under `data/derived`;
- runtime data remains outside Git;
- schema or semantic changes must be explicit and migratable.

In the current code, `raw` means normalized source observations. It does not promise byte-for-byte
HTTP payload retention.

## Consequences

- Collection and analysis stay easy to inspect with pandas, PyArrow, and other local tools.
- No database service, migration server, or distributed scheduler is required for M1/M2.
- Atomic multi-file runs, schema versioning, manifests, locking, and recovery are not supplied by
  Parquet itself and must be added where M2 evidence requires them.
- A future database is justified by measured concurrency, integrity, or query needs and should
  supersede this ADR rather than appear as incidental infrastructure.

