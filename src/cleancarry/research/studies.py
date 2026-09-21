from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .. import __version__
from ..config import Settings
from .replay import (
    MARKET_COLUMNS,
    ReplayConfig,
    ReplayResult,
    ReplaySummary,
    TradeRecord,
    prepare_signals,
    run_hysteresis_replay,
)

STUDY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class StudyConfig:
    coin: str
    as_of_utc: datetime | None = None
    notional_usd: float = 1_000.0
    entry_net_apr: float = 0.10
    exit_net_apr: float = 0.05
    minimum_trades_for_decision: int = 20

    def __post_init__(self) -> None:
        normalized_coin = self.coin.strip().upper()
        if not normalized_coin:
            raise ValueError("coin cannot be empty")
        object.__setattr__(self, "coin", normalized_coin)
        if self.as_of_utc is not None and self.as_of_utc.tzinfo is None:
            raise ValueError("as_of_utc must be timezone-aware")
        if self.minimum_trades_for_decision < 1:
            raise ValueError("minimum_trades_for_decision must be positive")


@dataclass(frozen=True)
class ArchiveInput:
    path: str
    sha256: str
    size_bytes: int
    selected_rows: int
    first_observed_at_utc: str
    last_observed_at_utc: str

    def asdict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StudyResult:
    study_id: str
    output_dir: Path
    manifest_path: Path
    decision: str
    decision_reason: str
    summaries: dict[str, ReplaySummary]


def load_archived_market_observations(
    derived_dir: Path,
    *,
    coin: str,
    as_of_utc: datetime | None,
) -> tuple[pd.DataFrame, tuple[ArchiveInput, ...]]:
    """Load selected carry snapshots, excluding every observation after the decision cutoff."""
    paths = sorted(derived_dir.glob("carry_markets_*.parquet"))
    if not paths:
        raise ValueError(f"no carry market snapshots found in {derived_dir}")

    selected: list[pd.DataFrame] = []
    inputs: list[ArchiveInput] = []
    normalized_coin = coin.strip().upper()
    cutoff = _utc_timestamp(as_of_utc) if as_of_utc is not None else None

    for path in paths:
        frame = pd.read_parquet(path)
        missing = sorted(MARKET_COLUMNS - set(frame.columns))
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(missing)}")
        observed = pd.to_datetime(frame["observed_at_utc"], utc=True, errors="coerce")
        if observed.isna().any():
            raise ValueError(f"{path} contains invalid observed_at_utc values")
        frame = frame.assign(observed_at_utc=observed)
        frame = frame[frame["coin"].astype(str).str.upper() == normalized_coin]
        if cutoff is not None:
            frame = frame[frame["observed_at_utc"] <= cutoff]
        if frame.empty:
            continue

        selected.append(frame)
        inputs.append(
            ArchiveInput(
                path=path.name,
                sha256=_file_sha256(path),
                size_bytes=path.stat().st_size,
                selected_rows=len(frame),
                first_observed_at_utc=frame["observed_at_utc"].min().isoformat(),
                last_observed_at_utc=frame["observed_at_utc"].max().isoformat(),
            )
        )

    if not selected:
        suffix = f" at or before {cutoff.isoformat()}" if cutoff is not None else ""
        raise ValueError(f"no archived observations found for {normalized_coin}{suffix}")

    observations = pd.concat(selected, ignore_index=True)
    observations = observations.sort_values("observed_at_utc", kind="stable").reset_index(
        drop=True
    )
    if observations.duplicated(subset=["coin", "observed_at_utc"]).any():
        raise ValueError("duplicate coin/observation timestamps are ambiguous")
    return observations, tuple(inputs)


def run_carry_study(
    *,
    derived_dir: Path,
    output_root: Path,
    settings: Settings,
    config: StudyConfig,
) -> StudyResult:
    """Run and persist the first reproducible ensemble-vs-current carry replay slice."""
    observations, inputs = load_archived_market_observations(
        derived_dir,
        coin=config.coin,
        as_of_utc=config.as_of_utc,
    )
    replay_config = ReplayConfig(
        notional_usd=config.notional_usd,
        entry_net_apr=config.entry_net_apr,
        exit_net_apr=config.exit_net_apr,
        round_trip_fee_bps=settings.round_trip_fees_bps,
    )

    results: dict[str, ReplayResult] = {}
    for model in ("ensemble", "current_only"):
        signals = prepare_signals(observations, settings, model)
        results[model] = run_hysteresis_replay(signals, model=model, config=replay_config)

    decision, reason = _research_decision(
        results["ensemble"].summary,
        results["current_only"].summary,
        config.minimum_trades_for_decision,
    )
    created_at = datetime.now(UTC)
    study_id = f"carry_{config.coin}_{created_at.strftime('%Y%m%dT%H%M%S%fZ')}"
    output_dir = output_root / study_id
    output_dir.mkdir(parents=True, exist_ok=False)

    output_files: dict[str, dict[str, object]] = {}
    for model, result in results.items():
        trade_path = output_dir / f"trades_{model}.parquet"
        _write_trades(trade_path, result.trades)
        output_files[f"trades_{model}"] = _output_identity(trade_path)

    summaries = {name: result.summary.asdict() for name, result in results.items()}
    summary_path = output_dir / "summary.json"
    _write_json_atomic(
        summary_path,
        {
            "schema_version": STUDY_SCHEMA_VERSION,
            "study_id": study_id,
            "decision": decision,
            "decision_reason": reason,
            "models": summaries,
        },
    )
    output_files["summary"] = _output_identity(summary_path)

    manifest = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_type": "single_pair_hysteresis_replay",
        "study_id": study_id,
        "created_at_utc": created_at.isoformat(),
        "package_version": __version__,
        "source_tree_sha256": _source_tree_sha256(),
        "hypothesis": (
            "The funding ensemble produces positive net paired-leg P&L and improves on a "
            "current-funding-only signal under identical cost assumptions."
        ),
        "decision_rule": {
            "minimum_ensemble_trades": config.minimum_trades_for_decision,
            "go_when": "ensemble net P&L > 0 and ensemble net P&L > current-only net P&L",
            "otherwise": "KILL when sample is sufficient; INSUFFICIENT_DATA otherwise",
        },
        "decision": decision,
        "decision_reason": reason,
        "study_config": _study_config_dict(config),
        "replay_config": asdict(replay_config),
        "data_identity": {
            "dataset_sha256": _dataset_sha256(inputs),
            "input_files": [item.asdict() for item in inputs],
            "observation_count": len(observations),
            "first_observed_at_utc": observations["observed_at_utc"].min().isoformat(),
            "last_observed_at_utc": observations["observed_at_utc"].max().isoformat(),
            "effective_as_of_utc": (
                _utc_timestamp(config.as_of_utc).isoformat()
                if config.as_of_utc is not None
                else observations["observed_at_utc"].max().isoformat()
            ),
        },
        "models": summaries,
        "outputs": output_files,
        "limitations": [
            "Funding is accrued from the rate known at the start of each observed interval.",
            "Quoted full spreads approximate paired entry/exit slippage; market impact is absent.",
            "When an exit spread is unavailable, the corresponding entry spread is reused.",
            "Closed-trade drawdown is not intratrade mark-to-market drawdown.",
            "Input snapshots are sequential observations rather than atomic venue snapshots.",
        ],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json_atomic(manifest_path, manifest)

    return StudyResult(
        study_id=study_id,
        output_dir=output_dir,
        manifest_path=manifest_path,
        decision=decision,
        decision_reason=reason,
        summaries={name: result.summary for name, result in results.items()},
    )


def _research_decision(
    ensemble: ReplaySummary,
    current_only: ReplaySummary,
    minimum_trades: int,
) -> tuple[str, str]:
    if ensemble.trade_count < minimum_trades:
        return (
            "INSUFFICIENT_DATA",
            f"ensemble closed {ensemble.trade_count} trades; {minimum_trades} required",
        )
    if (
        ensemble.total_net_pnl_usd > 0
        and ensemble.total_net_pnl_usd > current_only.total_net_pnl_usd
    ):
        return "GO", "ensemble net P&L is positive and exceeds the current-only baseline"
    return "KILL", "ensemble failed the pre-registered net-P&L comparison rule"


def _study_config_dict(config: StudyConfig) -> dict[str, object]:
    return {
        "coin": config.coin,
        "as_of_utc": (
            _utc_timestamp(config.as_of_utc).isoformat()
            if config.as_of_utc is not None
            else None
        ),
        "notional_usd": config.notional_usd,
        "entry_net_apr": config.entry_net_apr,
        "exit_net_apr": config.exit_net_apr,
        "minimum_trades_for_decision": config.minimum_trades_for_decision,
    }


def _write_trades(path: Path, trades: tuple[TradeRecord, ...]) -> None:
    columns = [field.name for field in fields(TradeRecord)]
    frame = pd.DataFrame([trade.asdict() for trade in trades], columns=columns)
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".json",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _output_identity(path: Path) -> dict[str, object]:
    return {
        "path": path.name,
        "sha256": _file_sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_sha256(inputs: tuple[ArchiveInput, ...]) -> str:
    digest = hashlib.sha256()
    for item in inputs:
        digest.update(item.path.encode())
        digest.update(b"\0")
        digest.update(item.sha256.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _source_tree_sha256() -> str:
    package_root = Path(__file__).parents[1]
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def _utc_timestamp(value: datetime) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_convert("UTC")
