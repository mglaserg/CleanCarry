from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Literal

import pandas as pd

from ..config import Settings
from ..models import CarryMarket
from ..scanner import HOURS_PER_YEAR, score_market
from .accounting import TradeAttribution, attribute_trade

SignalModel = Literal["ensemble", "current_only"]

MARKET_COLUMNS = {
    "coin",
    "spot_market",
    "observed_at_utc",
    "spot_mid",
    "perp_mid",
    "spot_day_volume_usd",
    "perp_day_volume_usd",
    "current_funding_hourly",
    "predicted_funding_hourly",
    "ewma_24h",
    "ewma_72h",
    "funding_vol_72h",
    "basis_bps",
    "spot_spread_bps",
    "perp_spread_bps",
}


@dataclass(frozen=True)
class ReplayConfig:
    notional_usd: float = 1_000.0
    entry_net_apr: float = 0.10
    exit_net_apr: float = 0.05
    round_trip_fee_bps: float = 12.0

    def __post_init__(self) -> None:
        if self.notional_usd <= 0:
            raise ValueError("notional_usd must be positive")
        if self.exit_net_apr >= self.entry_net_apr:
            raise ValueError("exit_net_apr must be below entry_net_apr")
        if self.round_trip_fee_bps < 0:
            raise ValueError("round_trip_fee_bps cannot be negative")


@dataclass(frozen=True)
class TradeRecord:
    model: SignalModel
    coin: str
    entry_at_utc: str
    exit_at_utc: str
    duration_hours: float
    notional_usd: float
    entry_spot_px: float
    exit_spot_px: float
    entry_perp_px: float
    exit_perp_px: float
    entry_signal_net_apr: float
    exit_signal_net_apr: float
    exit_reason: str
    spot_pnl_usd: float
    perp_pnl_usd: float
    funding_pnl_usd: float
    fee_cost_usd: float
    slippage_cost_usd: float
    net_pnl_usd: float

    def asdict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReplaySummary:
    model: SignalModel
    observation_count: int
    trade_count: int
    total_spot_pnl_usd: float
    total_perp_pnl_usd: float
    total_funding_pnl_usd: float
    total_fee_cost_usd: float
    total_slippage_cost_usd: float
    total_net_pnl_usd: float
    win_rate: float | None
    closed_trade_max_drawdown_usd: float

    def asdict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayResult:
    summary: ReplaySummary
    trades: tuple[TradeRecord, ...]


@dataclass
class _OpenPosition:
    entry_at: pd.Timestamp
    entry_spot_px: float
    entry_perp_px: float
    entry_signal_net_apr: float
    entry_spot_spread_bps: float
    entry_perp_spread_bps: float
    perp_quantity: float
    funding_pnl_usd: float = 0.0


def prepare_signals(
    observations: pd.DataFrame,
    settings: Settings,
    model: SignalModel,
) -> pd.DataFrame:
    """Add a model-specific net-APR signal and pre-hurdle eligibility to observations."""
    missing = sorted(MARKET_COLUMNS - set(observations.columns))
    if missing:
        raise ValueError(f"carry observations are missing columns: {', '.join(missing)}")
    if model not in ("ensemble", "current_only"):
        raise ValueError(f"unknown signal model: {model}")

    output = observations.copy()
    signals: list[float] = []
    eligibility: list[bool] = []
    settings_without_hurdle = replace(settings, min_net_apr=float("-inf"))

    for row in output.to_dict(orient="records"):
        market = _market_from_row(row)
        if model == "ensemble":
            opportunity = score_market(market, settings_without_hurdle)
            signals.append(opportunity.expected_net_apr)
            eligibility.append(opportunity.eligible)
            continue

        spread_cost_bps = _required_spread(market.spot_spread_bps) + _required_spread(
            market.perp_spread_bps
        )
        cost_apr = (
            (settings.round_trip_fees_bps + spread_cost_bps)
            / 10_000.0
            * HOURS_PER_YEAR
            / max(settings.expected_hold_hours, 1)
        )
        signal = (
            market.current_funding_hourly * HOURS_PER_YEAR
            - cost_apr
            - settings.basis_risk_buffer_apr
        )
        signals.append(signal)
        eligibility.append(_current_only_eligible(market, settings))

    output["signal_net_apr"] = signals
    output["signal_eligible"] = eligibility
    return _validate_signal_frame(output)


def run_hysteresis_replay(
    observations: pd.DataFrame,
    *,
    model: SignalModel,
    config: ReplayConfig,
) -> ReplayResult:
    """Replay one coin using only each row and the previously observed interval state."""
    frame = _validate_signal_frame(observations)
    coins = frame["coin"].dropna().astype(str).unique().tolist()
    if len(coins) != 1:
        raise ValueError("replay requires observations for exactly one coin")
    coin = coins[0]

    trades: list[TradeRecord] = []
    position: _OpenPosition | None = None
    previous: dict[str, object] | None = None
    records = frame.to_dict(orient="records")

    for index, row in enumerate(records):
        observed_at = pd.Timestamp(row["observed_at_utc"])
        if position is not None and previous is not None:
            previous_at = pd.Timestamp(previous["observed_at_utc"])
            interval_hours = (observed_at - previous_at).total_seconds() / 3_600.0
            previous_rate = float(previous["current_funding_hourly"])
            previous_perp_px = float(previous["perp_mid"])
            if interval_hours < 0:
                raise ValueError("observations must be chronological")
            if math.isfinite(previous_rate):
                previous_perp_notional = position.perp_quantity * previous_perp_px
                position.funding_pnl_usd += (
                    previous_perp_notional * previous_rate * interval_hours
                )

        should_exit = position is not None and (
            not bool(row["signal_eligible"])
            or float(row["signal_net_apr"]) < config.exit_net_apr
        )
        if position is not None and should_exit:
            reason = "ineligible" if not bool(row["signal_eligible"]) else "exit_hurdle"
            trades.append(_close_position(position, row, coin, model, config, reason))
            position = None

        can_enter = (
            position is None
            and index < len(records) - 1
            and bool(row["signal_eligible"])
            and float(row["signal_net_apr"]) >= config.entry_net_apr
        )
        if can_enter:
            entry_perp_px = float(row["perp_mid"])
            position = _OpenPosition(
                entry_at=observed_at,
                entry_spot_px=float(row["spot_mid"]),
                entry_perp_px=entry_perp_px,
                entry_signal_net_apr=float(row["signal_net_apr"]),
                entry_spot_spread_bps=float(row["spot_spread_bps"]),
                entry_perp_spread_bps=float(row["perp_spread_bps"]),
                perp_quantity=config.notional_usd / entry_perp_px,
            )
        previous = row

    if position is not None:
        trades.append(
            _close_position(position, records[-1], coin, model, config, "end_of_data")
        )

    summary = _summarize(model, len(frame), trades)
    return ReplayResult(summary=summary, trades=tuple(trades))


def _close_position(
    position: _OpenPosition,
    row: dict[str, object],
    coin: str,
    model: SignalModel,
    config: ReplayConfig,
    reason: str,
) -> TradeRecord:
    exit_spot_spread = _exit_spread(row["spot_spread_bps"], position.entry_spot_spread_bps)
    exit_perp_spread = _exit_spread(row["perp_spread_bps"], position.entry_perp_spread_bps)
    round_trip_slippage_bps = (
        position.entry_spot_spread_bps
        + position.entry_perp_spread_bps
        + exit_spot_spread
        + exit_perp_spread
    ) / 2.0
    attribution = attribute_trade(
        notional_usd=config.notional_usd,
        entry_spot_px=position.entry_spot_px,
        exit_spot_px=float(row["spot_mid"]),
        entry_perp_px=position.entry_perp_px,
        exit_perp_px=float(row["perp_mid"]),
        funding_pnl_usd=position.funding_pnl_usd,
        round_trip_fee_bps=config.round_trip_fee_bps,
        round_trip_slippage_bps=round_trip_slippage_bps,
    )
    return _trade_record(position, row, coin, model, config, reason, attribution)


def _trade_record(
    position: _OpenPosition,
    row: dict[str, object],
    coin: str,
    model: SignalModel,
    config: ReplayConfig,
    reason: str,
    attribution: TradeAttribution,
) -> TradeRecord:
    exit_at = pd.Timestamp(row["observed_at_utc"])
    return TradeRecord(
        model=model,
        coin=coin,
        entry_at_utc=position.entry_at.isoformat(),
        exit_at_utc=exit_at.isoformat(),
        duration_hours=(exit_at - position.entry_at).total_seconds() / 3_600.0,
        notional_usd=config.notional_usd,
        entry_spot_px=position.entry_spot_px,
        exit_spot_px=float(row["spot_mid"]),
        entry_perp_px=position.entry_perp_px,
        exit_perp_px=float(row["perp_mid"]),
        entry_signal_net_apr=position.entry_signal_net_apr,
        exit_signal_net_apr=float(row["signal_net_apr"]),
        exit_reason=reason,
        **asdict(attribution),
    )


def _summarize(
    model: SignalModel,
    observation_count: int,
    trades: list[TradeRecord],
) -> ReplaySummary:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.net_pnl_usd
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    def total(field: str) -> float:
        return sum(float(getattr(trade, field)) for trade in trades)

    wins = sum(trade.net_pnl_usd > 0 for trade in trades)
    return ReplaySummary(
        model=model,
        observation_count=observation_count,
        trade_count=len(trades),
        total_spot_pnl_usd=total("spot_pnl_usd"),
        total_perp_pnl_usd=total("perp_pnl_usd"),
        total_funding_pnl_usd=total("funding_pnl_usd"),
        total_fee_cost_usd=total("fee_cost_usd"),
        total_slippage_cost_usd=total("slippage_cost_usd"),
        total_net_pnl_usd=total("net_pnl_usd"),
        win_rate=wins / len(trades) if trades else None,
        closed_trade_max_drawdown_usd=max_drawdown,
    )


def _market_from_row(row: dict[str, object]) -> CarryMarket:
    return CarryMarket(
        coin=str(row["coin"]),
        spot_market=str(row["spot_market"]),
        spot_mid=float(row["spot_mid"]),
        perp_mid=float(row["perp_mid"]),
        spot_day_volume_usd=float(row["spot_day_volume_usd"]),
        perp_day_volume_usd=float(row["perp_day_volume_usd"]),
        current_funding_hourly=float(row["current_funding_hourly"]),
        predicted_funding_hourly=_optional_float(row["predicted_funding_hourly"]),
        ewma_24h=_optional_float(row["ewma_24h"]),
        ewma_72h=_optional_float(row["ewma_72h"]),
        funding_vol_72h=_optional_float(row["funding_vol_72h"]),
        basis_bps=float(row["basis_bps"]),
        spot_spread_bps=_optional_float(row["spot_spread_bps"]),
        perp_spread_bps=_optional_float(row["perp_spread_bps"]),
    )


def _current_only_eligible(market: CarryMarket, settings: Settings) -> bool:
    spreads = (market.spot_spread_bps, market.perp_spread_bps)
    return (
        market.spot_day_volume_usd >= settings.min_spot_day_volume_usd
        and market.perp_day_volume_usd >= settings.min_perp_day_volume_usd
        and abs(market.basis_bps) <= settings.max_abs_basis_bps
        and market.current_funding_hourly > 0
        and all(
            spread is not None and 0 <= spread <= settings.max_spread_bps_per_leg
            for spread in spreads
        )
    )


def _validate_signal_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = MARKET_COLUMNS | {"signal_net_apr", "signal_eligible"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"signal observations are missing columns: {', '.join(missing)}")
    output = frame.copy()
    output["observed_at_utc"] = pd.to_datetime(
        output["observed_at_utc"], utc=True, errors="coerce"
    )
    if output["observed_at_utc"].isna().any():
        raise ValueError("observed_at_utc contains invalid timestamps")
    output = output.sort_values("observed_at_utc", kind="stable").reset_index(drop=True)
    if output.duplicated(subset=["coin", "observed_at_utc"]).any():
        raise ValueError("duplicate coin/observation timestamps are ambiguous")
    numeric = [
        "spot_mid",
        "perp_mid",
        "current_funding_hourly",
        "signal_net_apr",
        "spot_spread_bps",
        "perp_spread_bps",
    ]
    for column in numeric:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    finite_columns = ["spot_mid", "perp_mid", "current_funding_hourly", "signal_net_apr"]
    finite = output[finite_columns].apply(lambda series: series.map(math.isfinite))
    if not finite.all().all():
        raise ValueError("prices, funding, and signal_net_apr must be finite")
    if (output[["spot_mid", "perp_mid"]] <= 0).any().any():
        raise ValueError("prices must be positive")
    spreads = output[["spot_spread_bps", "perp_spread_bps"]]
    if (spreads.dropna() < 0).any().any():
        raise ValueError("observed spreads cannot be negative")
    return output


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _required_spread(value: float | None) -> float:
    return value if value is not None and math.isfinite(value) else 0.0


def _exit_spread(value: object, fallback: float) -> float:
    parsed = _optional_float(value)
    return fallback if parsed is None else parsed
