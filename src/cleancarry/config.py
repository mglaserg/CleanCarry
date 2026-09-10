from __future__ import annotations

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


@dataclass(frozen=True)
class Settings:
    base_url: str = os.getenv("HL_BASE_URL", "https://api.hyperliquid.xyz")
    account_address: str | None = os.getenv("HL_ACCOUNT_ADDRESS") or None

    data_dir: Path = Path(os.getenv("CLEANCARRY_DATA_DIR", "data"))
    state_dir: Path = Path(os.getenv("CLEANCARRY_STATE_DIR", "state"))
    log_dir: Path = Path(os.getenv("CLEANCARRY_LOG_DIR", "logs"))

    min_spot_day_volume_usd: float = _float("CC_MIN_SPOT_DAY_VOLUME_USD", 250_000)
    min_perp_day_volume_usd: float = _float("CC_MIN_PERP_DAY_VOLUME_USD", 1_000_000)
    min_net_apr: float = _float("CC_MIN_NET_APR", 0.10)
    exit_net_apr: float = _float("CC_EXIT_NET_APR", 0.05)
    max_abs_basis_bps: float = _float("CC_MAX_ABS_BASIS_BPS", 300)
    max_spread_bps_per_leg: float = _float("CC_MAX_SPREAD_BPS_PER_LEG", 35)
    expected_hold_hours: int = _int("CC_EXPECTED_HOLD_HOURS", 72)
    round_trip_fees_bps: float = _float("CC_ROUND_TRIP_FEES_BPS", 12)
    basis_risk_buffer_apr: float = _float("CC_BASIS_RISK_BUFFER_APR", 0.02)
    history_hours: int = _int("CC_HISTORY_HOURS", 168)
    history_candidates: int = _int("CC_HISTORY_CANDIDATES", 25)
    book_candidates: int = _int("CC_BOOK_CANDIDATES", 15)
    spot_to_perp_aliases: str = os.getenv("CC_SPOT_TO_PERP_ALIASES", "UBTC:BTC")

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

    def ensure_dirs(self) -> None:
        for path in (self.data_dir / "raw", self.data_dir / "derived", self.state_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)
