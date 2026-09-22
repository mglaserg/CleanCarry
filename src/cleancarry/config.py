from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load a project-local .env for interactive use and the canonical Lubuntu path when installed.
load_dotenv()
load_dotenv("/opt/cleancarry/.env", override=False)


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str) -> tuple[str, ...]:
    return tuple(
        sorted({item.strip().upper() for item in os.getenv(name, "").split(",") if item.strip()})
    )


@dataclass(frozen=True)
class Settings:
    base_url: str = os.getenv("HL_BASE_URL", "https://api.hyperliquid.xyz")
    account_address: str | None = os.getenv("HL_ACCOUNT_ADDRESS") or None
    api_wallet_private_key: str | None = os.getenv("HL_API_WALLET_PRIVATE_KEY") or None
    subaccount_address: str | None = os.getenv("HL_SUBACCOUNT_ADDRESS") or None

    data_dir: Path = Path(os.getenv("CLEANCARRY_DATA_DIR", "data"))
    state_dir: Path = Path(os.getenv("CLEANCARRY_STATE_DIR", "state"))
    log_dir: Path = Path(os.getenv("CLEANCARRY_LOG_DIR", "logs"))

    min_spot_day_volume_usd: float = _float("CC_MIN_SPOT_DAY_VOLUME_USD", 50_000_000)
    min_perp_day_volume_usd: float = _float("CC_MIN_PERP_DAY_VOLUME_USD", 50_000_000)
    min_perp_open_interest_usd: float = _float("CC_MIN_PERP_OPEN_INTEREST_USD", 0)
    min_depth_usd_per_leg: float = _float("CC_MIN_DEPTH_USD_PER_LEG", 10_000)
    depth_impact_bps: float = _float("CC_DEPTH_IMPACT_BPS", 20)
    min_net_apr: float = _float("CC_MIN_NET_APR", 0.30)
    exit_net_apr: float = _float("CC_EXIT_NET_APR", 0.10)
    max_abs_basis_bps: float = _float("CC_MAX_ABS_BASIS_BPS", 300)
    max_spread_bps_per_leg: float = _float("CC_MAX_SPREAD_BPS_PER_LEG", 35)
    expected_hold_hours: int = _int("CC_EXPECTED_HOLD_HOURS", 168)
    round_trip_fees_bps: float = _float("CC_ROUND_TRIP_FEES_BPS", 20)
    basis_risk_buffer_apr: float = _float("CC_BASIS_RISK_BUFFER_APR", 0.02)
    history_hours: int = _int("CC_HISTORY_HOURS", 168)
    funding_forecast_method: str = os.getenv(
        "CC_FUNDING_FORECAST_METHOD", "rw_mean_96h"
    ).strip().lower()
    funding_mean_hours: int = _int("CC_FUNDING_MEAN_HOURS", 96)
    volume_lookback_days: int = _int("CC_VOLUME_LOOKBACK_DAYS", 5)
    min_funding_history: int = _int("CC_MIN_FUNDING_HISTORY", 96)
    history_candidates: int = _int("CC_HISTORY_CANDIDATES", 1000)
    book_candidates: int = _int("CC_BOOK_CANDIDATES", 1000)
    spot_to_perp_aliases: str = os.getenv("CC_SPOT_TO_PERP_ALIASES", "UBTC:BTC")

    universe_refresh_seconds: int = _int("CC_UNIVERSE_REFRESH_SECONDS", 3600)
    strategy_cycle_seconds: int = _int("CC_STRATEGY_CYCLE_SECONDS", 300)
    stale_data_seconds: int = _int("CC_STALE_DATA_SECONDS", 120)
    order_timeout_seconds: int = _int("CC_ORDER_TIMEOUT_SECONDS", 30)
    execution_mode: str = os.getenv("CC_EXECUTION_MODE", "read_only").strip().lower()
    allowlist: tuple[str, ...] = _csv("CC_ALLOWLIST")
    denylist: tuple[str, ...] = _csv("CC_DENYLIST")

    strategy_capital_usd: float = _float("CC_STRATEGY_CAPITAL_USD", 10_000)
    max_positions: int = _int("CC_MAX_POSITIONS", 20)
    max_total_deployment_usd: float = _float("CC_MAX_TOTAL_DEPLOYMENT_USD", 10_000)
    max_per_asset_usd: float = _float("CC_MAX_PER_ASSET_USD", 2_500)
    perp_margin_fraction: float = _float("CC_PERP_MARGIN_FRACTION", 0.20)
    max_margin_utilization: float = _float("CC_MAX_MARGIN_UTILIZATION", 0.50)
    liquidity_participation: float = _float("CC_LIQUIDITY_PARTICIPATION", 0.01)
    hedge_tolerance_bps: float = _float("CC_HEDGE_TOLERANCE_BPS", 50)
    trade_buffer_usd: float = _float("CC_TRADE_BUFFER_USD", 25)
    trade_buffer_fraction: float = _float("CC_TRADE_BUFFER_FRACTION", 0.03)

    def alias_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in self.spot_to_perp_aliases.split(","):
            if ":" not in item:
                continue
            spot, perp = (x.strip() for x in item.split(":", 1))
            if spot and perp:
                out[spot] = perp
        return out

    live_trading_enabled: bool = _bool("LIVE_TRADING_ENABLED", False)
    live_max_order_usd: float = _float("CC_LIVE_MAX_ORDER_USD", 100.0)
    live_max_total_deployment_usd: float = _float("CC_LIVE_MAX_TOTAL_DEPLOYMENT_USD", 1_000.0)
    live_min_order_usd: float = _float("CC_LIVE_MIN_ORDER_USD", 10.0)
    live_capital_fraction: float = _float("CC_LIVE_CAPITAL_FRACTION", 0.25)
    live_slippage_bps: float = _float("CC_LIVE_SLIPPAGE_BPS", 20.0)

    def __post_init__(self) -> None:
        if self.execution_mode not in {"read_only", "shadow", "live"}:
            raise ValueError("CC_EXECUTION_MODE must be read_only, shadow, or live")
        if self.funding_forecast_method not in {"rw_mean_96h", "legacy_ensemble"}:
            raise ValueError(
                "CC_FUNDING_FORECAST_METHOD must be rw_mean_96h or legacy_ensemble"
            )
        positive = {
            "CC_EXPECTED_HOLD_HOURS": self.expected_hold_hours,
            "CC_HISTORY_HOURS": self.history_hours,
            "CC_FUNDING_MEAN_HOURS": self.funding_mean_hours,
            "CC_VOLUME_LOOKBACK_DAYS": self.volume_lookback_days,
            "CC_MIN_FUNDING_HISTORY": self.min_funding_history,
            "CC_HISTORY_CANDIDATES": self.history_candidates,
            "CC_BOOK_CANDIDATES": self.book_candidates,
            "CC_UNIVERSE_REFRESH_SECONDS": self.universe_refresh_seconds,
            "CC_STRATEGY_CYCLE_SECONDS": self.strategy_cycle_seconds,
            "CC_STALE_DATA_SECONDS": self.stale_data_seconds,
            "CC_ORDER_TIMEOUT_SECONDS": self.order_timeout_seconds,
            "CC_STRATEGY_CAPITAL_USD": self.strategy_capital_usd,
            "CC_MAX_POSITIONS": self.max_positions,
            "CC_MAX_TOTAL_DEPLOYMENT_USD": self.max_total_deployment_usd,
            "CC_MAX_PER_ASSET_USD": self.max_per_asset_usd,
            "CC_LIVE_MAX_ORDER_USD": self.live_max_order_usd,
            "CC_LIVE_MAX_TOTAL_DEPLOYMENT_USD": self.live_max_total_deployment_usd,
            "CC_LIVE_MIN_ORDER_USD": self.live_min_order_usd,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        nonnegative = {
            "CC_MIN_SPOT_DAY_VOLUME_USD": self.min_spot_day_volume_usd,
            "CC_MIN_PERP_DAY_VOLUME_USD": self.min_perp_day_volume_usd,
            "CC_MIN_PERP_OPEN_INTEREST_USD": self.min_perp_open_interest_usd,
            "CC_MIN_DEPTH_USD_PER_LEG": self.min_depth_usd_per_leg,
            "CC_ROUND_TRIP_FEES_BPS": self.round_trip_fees_bps,
            "CC_BASIS_RISK_BUFFER_APR": self.basis_risk_buffer_apr,
            "CC_HEDGE_TOLERANCE_BPS": self.hedge_tolerance_bps,
            "CC_TRADE_BUFFER_USD": self.trade_buffer_usd,
            "CC_LIVE_SLIPPAGE_BPS": self.live_slippage_bps,
        }
        for name, value in nonnegative.items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if math.isfinite(self.min_net_apr) and self.exit_net_apr >= self.min_net_apr:
            raise ValueError("CC_EXIT_NET_APR must be below CC_MIN_NET_APR")
        for name, value in (
            ("CC_PERP_MARGIN_FRACTION", self.perp_margin_fraction),
            ("CC_MAX_MARGIN_UTILIZATION", self.max_margin_utilization),
            ("CC_LIQUIDITY_PARTICIPATION", self.liquidity_participation),
            ("CC_TRADE_BUFFER_FRACTION", self.trade_buffer_fraction),
            ("CC_LIVE_CAPITAL_FRACTION", self.live_capital_fraction),
        ):
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        overlap = set(self.allowlist) & set(self.denylist)
        if overlap:
            raise ValueError(f"allowlist and denylist overlap: {', '.join(sorted(overlap))}")

    def ensure_dirs(self) -> None:
        for path in (self.data_dir / "raw", self.data_dir / "derived", self.state_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)
