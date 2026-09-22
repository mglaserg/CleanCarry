from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class RejectionCode:
    NO_SPOT_HEDGE = "NO_SPOT_HEDGE"
    INVALID_MARKET_DATA = "INVALID_MARKET_DATA"
    INSUFFICIENT_VOLUME = "INSUFFICIENT_VOLUME"
    INSUFFICIENT_OPEN_INTEREST = "INSUFFICIENT_OPEN_INTEREST"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    STALE_DATA = "STALE_DATA"
    FUNDING_TOO_LOW = "FUNDING_TOO_LOW"
    EXPECTED_NET_CARRY_TOO_LOW = "EXPECTED_NET_CARRY_TOO_LOW"
    BASIS_TOO_WIDE = "BASIS_TOO_WIDE"
    CAPACITY_TOO_LOW = "CAPACITY_TOO_LOW"
    ALLOWLIST = "ALLOWLIST"
    DENYLIST = "DENYLIST"
    ELIGIBLE = "ELIGIBLE"


@dataclass(frozen=True)
class CarryMarket:
    coin: str
    spot_market: str
    spot_mid: float
    perp_mid: float
    spot_day_volume_usd: float
    perp_day_volume_usd: float
    current_funding_hourly: float
    predicted_funding_hourly: float | None
    ewma_24h: float | None
    ewma_72h: float | None
    funding_vol_72h: float | None
    basis_bps: float
    spot_token: str | None = None
    mean_funding_hourly: float | None = None
    spot_spread_bps: float | None = None
    perp_spread_bps: float | None = None
    observed_at_utc: str | None = None
    perp_open_interest_usd: float | None = None
    spot_depth_usd: float | None = None
    perp_depth_usd: float | None = None
    funding_history_count: int = 0
    spot_average_day_volume_usd: float | None = None
    perp_average_day_volume_usd: float | None = None
    volume_history_days: int | None = None

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Opportunity:
    coin: str
    spot_market: str
    expected_funding_hourly: float
    gross_funding_apr: float
    estimated_round_trip_cost_bps: float
    cost_apr_at_expected_hold: float
    basis_risk_buffer_apr: float
    expected_net_apr: float
    basis_bps: float
    current_funding_apr: float
    predicted_funding_apr: float | None
    spot_day_volume_usd: float
    perp_day_volume_usd: float
    spot_spread_bps: float | None
    perp_spread_bps: float | None
    eligible: bool
    reason: str
    rejection_codes: tuple[str, ...] = ()
    capacity_usd: float = 0.0
    observed_at_utc: str | None = None
    forecast_method: str = "legacy_ensemble"

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UniverseEvaluation:
    coin: str
    spot_market: str | None
    stage: str
    eligible: bool
    rejection_codes: tuple[str, ...]
    detail: str
    observed_at_utc: str

    def asdict(self) -> dict[str, Any]:
        return asdict(self)
