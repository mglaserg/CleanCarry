from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils.types import Cloid

from .config import Settings

ARM_CONFIRMATION = "I_ACCEPT_LIVE_TRADING"


@dataclass
class LivePosition:
    coin: str
    spot_market: str
    spot_token: str
    quantity: float
    opened_at_utc: str
    target_notional_usd: float


@dataclass
class LiveState:
    schema_version: int = 1
    status: str = "DISARMED"
    account_address: str | None = None
    positions: dict[str, LivePosition] = field(default_factory=dict)
    pending_action: dict[str, Any] | None = None
    completed_action_ids: list[str] = field(default_factory=list)
    last_equity_usd: float | None = None
    high_water_equity_usd: float | None = None
    last_cycle_at_utc: str | None = None


class LiveStateStore:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "live_state.json"

    def load(self) -> LiveState:
        if not self.path.exists():
            return LiveState()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["positions"] = {
            coin: LivePosition(**position) for coin, position in payload.get("positions", {}).items()
        }
        return LiveState(**payload)

    def save(self, state: LiveState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)


@dataclass(frozen=True)
class Fill:
    quantity: float
    average_price: float
    order_id: int | None


class HyperliquidLiveExecutor:
    """Isolated signer/order adapter using an API wallet, never a withdrawal-capable main key."""

    def __init__(self, settings: Settings) -> None:
        if not settings.api_wallet_private_key:
            raise ValueError("HL_API_WALLET_PRIVATE_KEY is required for live execution")
        if not settings.account_address:
            raise ValueError("HL_ACCOUNT_ADDRESS is required for live execution")
        wallet = Account.from_key(settings.api_wallet_private_key)
        self.exchange = Exchange(
            wallet,
            settings.base_url,
            account_address=settings.account_address,
            vault_address=settings.subaccount_address,
            timeout=settings.order_timeout_seconds,
        )
        self.slippage = settings.live_slippage_bps / 10_000.0

    def market_open(self, market: str, is_buy: bool, quantity: float, action_id: str) -> Fill:
        response = self.exchange.market_open(
            market,
            is_buy,
            quantity,
            slippage=self.slippage,
            cloid=_cloid(action_id),
        )
        return _parse_fill(response)

    def market_close_perp(self, coin: str, quantity: float, action_id: str) -> Fill:
        response = self.exchange.market_close(
            coin,
            sz=quantity,
            slippage=self.slippage,
            cloid=_cloid(action_id),
        )
        return _parse_fill(response)


class LivePairExecutor:
    def __init__(
        self, settings: Settings, store: LiveStateStore, venue: HyperliquidLiveExecutor
    ) -> None:
        self.settings = settings
        self.store = store
        self.venue = venue

    def enter(
        self,
        state: LiveState,
        *,
        coin: str,
        spot_market: str,
        spot_token: str,
        quantity: float,
        target_notional_usd: float,
        action_id: str,
    ) -> LivePosition:
        self._begin(state, action_id, coin, "ENTER")
        spot = self.venue.market_open(spot_market, True, quantity, f"{action_id}:spot")
        try:
            perp = self.venue.market_open(coin, False, spot.quantity, f"{action_id}:perp")
        except Exception:
            self.venue.market_open(spot_market, False, spot.quantity, f"{action_id}:recover")
            state.pending_action = None
            state.status = "SAFE_MODE"
            self.store.save(state)
            raise
        matched = min(spot.quantity, perp.quantity)
        if matched <= 0:
            state.status = "SAFE_MODE"
            self.store.save(state)
            raise RuntimeError(f"{coin} entry produced no matched paired fill")
        if spot.quantity > matched:
            self.venue.market_open(
                spot_market, False, spot.quantity - matched, f"{action_id}:trim-spot"
            )
        if perp.quantity > matched:
            self.venue.market_close_perp(
                coin, perp.quantity - matched, f"{action_id}:trim-perp"
            )
        existing = state.positions.get(coin)
        position = LivePosition(
            coin=coin,
            spot_market=spot_market,
            spot_token=spot_token,
            quantity=matched + (existing.quantity if existing else 0.0),
            opened_at_utc=(existing.opened_at_utc if existing else datetime.now(UTC).isoformat()),
            target_notional_usd=target_notional_usd,
        )
        state.positions[coin] = position
        self._complete(state, action_id)
        return position

    def reduce(
        self,
        state: LiveState,
        *,
        coin: str,
        quantity: float,
        target_notional_usd: float,
        action_id: str,
    ) -> LivePosition | None:
        position = state.positions[coin]
        quantity = min(quantity, position.quantity)
        self._begin(state, action_id, coin, "REDUCE")
        spot = self.venue.market_open(position.spot_market, False, quantity, f"{action_id}:spot")
        try:
            perp = self.venue.market_close_perp(coin, spot.quantity, f"{action_id}:perp")
        except Exception:
            state.status = "SAFE_MODE"
            self.store.save(state)
            raise
        matched = min(spot.quantity, perp.quantity)
        mismatch = spot.quantity - perp.quantity
        if abs(mismatch) > max(quantity * 0.005, 1e-12):
            try:
                if mismatch > 0:
                    self.venue.market_open(
                        position.spot_market,
                        True,
                        mismatch,
                        f"{action_id}:recover-spot",
                    )
                else:
                    self.venue.market_open(
                        coin,
                        False,
                        -mismatch,
                        f"{action_id}:recover-perp",
                    )
            except Exception:  # noqa: BLE001 - any SDK/transport failure must persist SAFE_MODE
                state.status = "SAFE_MODE"
                self.store.save(state)
                raise RuntimeError(f"{coin} reduction recovery failed") from None
        remaining = max(position.quantity - matched, 0.0)
        if remaining <= 1e-12:
            del state.positions[coin]
            result = None
        else:
            position.quantity = remaining
            position.target_notional_usd = target_notional_usd
            result = position
        self._complete(state, action_id)
        return result

    def exit(self, state: LiveState, coin: str, action_id: str) -> None:
        position = state.positions[coin]
        self.reduce(
            state,
            coin=coin,
            quantity=position.quantity,
            target_notional_usd=0.0,
            action_id=action_id,
        )

    def _begin(self, state: LiveState, action_id: str, coin: str, action: str) -> None:
        if state.pending_action is not None:
            raise RuntimeError("unresolved live action exists; operator reconciliation required")
        state.pending_action = {"action_id": action_id, "coin": coin, "action": action}
        self.store.save(state)

    def _complete(self, state: LiveState, action_id: str) -> None:
        state.pending_action = None
        state.completed_action_ids.append(action_id)
        state.completed_action_ids = state.completed_action_ids[-1000:]
        self.store.save(state)


def account_equity_usd(
    perp_state: dict[str, Any],
    spot_state: dict[str, Any],
    spot_prices: dict[str, float],
) -> float:
    margin = perp_state.get("marginSummary", {})
    equity = float(margin.get("accountValue", 0) or 0)
    for balance in spot_state.get("balances", []):
        coin = str(balance.get("coin", ""))
        total = float(balance.get("total", 0) or 0)
        equity += total if coin == "USDC" else total * spot_prices.get(coin, 0.0)
    return equity


def _cloid(action_id: str) -> Cloid:
    return Cloid.from_str("0x" + hashlib.sha256(action_id.encode()).hexdigest()[:32])


def _parse_fill(response: Any) -> Fill:
    if not isinstance(response, dict) or response.get("status") != "ok":
        raise RuntimeError(f"Hyperliquid order rejected: {response!r}")
    statuses = response.get("response", {}).get("data", {}).get("statuses", [])
    for status in statuses:
        if "error" in status:
            raise RuntimeError(f"Hyperliquid order rejected: {status['error']}")
        filled = status.get("filled")
        if filled:
            return Fill(
                quantity=float(filled["totalSz"]),
                average_price=float(filled["avgPx"]),
                order_id=int(filled["oid"]) if filled.get("oid") is not None else None,
            )
    raise RuntimeError(f"Hyperliquid IOC order did not fill: {response!r}")
