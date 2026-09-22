# Architecture Decision Records

ADRs preserve durable decisions that future contributors should not casually reverse.

Create records as:

```text
NNNN-short-title.md
```

Each record contains:

- Status: Proposed / Accepted / Superseded;
- Date;
- Context;
- Decision;
- Consequences;
- Supersedes / Superseded by, when applicable.

Do not rewrite accepted history to match a new direction. Add a record that explicitly supersedes
the old decision.

## Index

- `0001-read-only-milestone-boundaries.md` — research must pass explicit evidence gates before
  execution capability is introduced.
- `0002-local-parquet-evidence-store.md` — local Parquet is the initial append-oriented research store.
- `0003-reproducible-replay-study-contract.md` — replay studies use strict as-of inputs and versioned,
  hashed artifacts with pre-registered decisions.
- `0004-autonomous-shadow-strategy-boundary.md` — the production decision pipeline may run in
  read-only or persistent shadow mode while live execution remains prohibited.
