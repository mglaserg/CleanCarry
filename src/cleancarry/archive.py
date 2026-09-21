from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def write_snapshot(rows: Iterable[Mapping[str, Any]], directory: Path, stem: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}_{utc_stamp()}.parquet"
    frame = pd.DataFrame(list(rows))
    if "observed_at_utc" not in frame.columns:
        frame["observed_at_utc"] = datetime.now(UTC).isoformat()
    frame.to_parquet(path, index=False)
    return path


def upsert_timeseries(
    rows: Iterable[Mapping[str, Any]],
    path: Path,
    subset: list[str],
    sort_by: list[str],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame(list(rows))
    if path.exists():
        old = pd.read_parquet(path)
        new = pd.concat([old, new], ignore_index=True)
    if not new.empty:
        if subset:
            new = new.drop_duplicates(subset=subset, keep="last")
        else:
            new = new.drop_duplicates(keep="last")
        if sort_by:
            new = new.sort_values(sort_by)
    new.to_parquet(path, index=False)
    return path
