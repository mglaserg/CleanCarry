from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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
    spot_spread_bps: float | None = None
    perp_spread_bps: float | None = None

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

    def asdict(self) -> dict[str, Any]:
        return asdict(self)
