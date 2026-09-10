from __future__ import annotations

import math
from typing import Any


def summarize_account(perp_state: Any, spot_state: Any) -> tuple[dict[str, float], list[dict], list[dict]]:
    margin = (perp_state or {}).get("marginSummary", {})
    perp_positions: list[dict] = []
    for wrapper in (perp_state or {}).get("assetPositions", []):
        position = wrapper.get("position", {})
        szi = float(position.get("szi", 0) or 0)
        if abs(szi) > 0:
            perp_positions.append(
                {
                    "coin": position.get("coin"),
                    "size": szi,
                    "entry_px": _finite(position.get("entryPx")),
                    "position_value": _finite(position.get("positionValue"), 0.0),
                    "unrealized_pnl": _finite(position.get("unrealizedPnl"), 0.0),
                    "margin_used": _finite(position.get("marginUsed"), 0.0),
                }
            )

    spot_balances: list[dict] = []
    for bal in (spot_state or {}).get("balances", []):
        total = _finite(bal.get("total"), 0.0)
        hold = _finite(bal.get("hold"), 0.0)
        if total != 0 or hold != 0:
            spot_balances.append(
                {
                    "coin": bal.get("coin"),
                    "total": total,
                    "hold": hold,
                    "available": total - hold,
                }
            )

    summary = {
        "account_value": _finite(margin.get("accountValue"), 0.0),
        "total_margin_used": _finite(margin.get("totalMarginUsed"), 0.0),
        "total_perp_notional": _finite(margin.get("totalNtlPos"), 0.0),
        "withdrawable": _finite((perp_state or {}).get("withdrawable"), 0.0),
    }
    value = summary["account_value"]
    summary["margin_utilization"] = summary["total_margin_used"] / value if value > 0 else math.nan
    return summary, perp_positions, spot_balances


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default
