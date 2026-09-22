from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def write_snapshot(rows: Iterable[Mapping[str, Any]], directory: Path, stem: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}_{utc_stamp()}.parquet"
    frame = pd.DataFrame(list(rows))
    if "observed_at_utc" not in frame.columns:
        frame["observed_at_utc"] = datetime.now(UTC).isoformat()
    _write_parquet_atomic(frame, path)
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
    _write_parquet_atomic(new, path)
    return path


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".parquet",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
