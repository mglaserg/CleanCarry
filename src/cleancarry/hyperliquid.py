from __future__ import annotations

import time
from typing import Any, Self

import httpx


class HyperliquidInfoClient:
    """Small read-only wrapper around Hyperliquid's public POST /info API."""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": "CleanCarry/0.1"})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def post_info(self, payload: dict[str, Any]) -> Any:
        response = self._client.post(f"{self.base_url}/info", json=payload)
        response.raise_for_status()
        return response.json()

    def perp_meta_and_contexts(self) -> Any:
        return self.post_info({"type": "metaAndAssetCtxs"})

    def spot_meta_and_contexts(self) -> Any:
        return self.post_info({"type": "spotMetaAndAssetCtxs"})

    def predicted_fundings(self) -> Any:
        return self.post_info({"type": "predictedFundings"})

    def funding_history(self, coin: str, start_ms: int, end_ms: int | None = None) -> Any:
        payload: dict[str, Any] = {"type": "fundingHistory", "coin": coin, "startTime": start_ms}
        if end_ms is not None:
            payload["endTime"] = end_ms
        return self.post_info(payload)

    def l2_book(self, market: str) -> Any:
        return self.post_info({"type": "l2Book", "coin": market})

    def clearinghouse_state(self, address: str) -> Any:
        return self.post_info({"type": "clearinghouseState", "user": address})

    def spot_clearinghouse_state(self, address: str) -> Any:
        return self.post_info({"type": "spotClearinghouseState", "user": address})

    def user_funding(self, address: str, start_ms: int, end_ms: int | None = None) -> Any:
        payload: dict[str, Any] = {"type": "userFunding", "user": address, "startTime": start_ms}
        if end_ms is not None:
            payload["endTime"] = end_ms
        return self.post_info(payload)

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)
