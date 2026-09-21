from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeAttribution:
    """Dollar P&L attribution for one closed delta-neutral carry position."""

    spot_pnl_usd: float
    perp_pnl_usd: float
    funding_pnl_usd: float
    fee_cost_usd: float
    slippage_cost_usd: float
    net_pnl_usd: float


def attribute_trade(
    *,
    notional_usd: float,
    entry_spot_px: float,
    exit_spot_px: float,
    entry_perp_px: float,
    exit_perp_px: float,
    funding_pnl_usd: float,
    round_trip_fee_bps: float,
    round_trip_slippage_bps: float,
) -> TradeAttribution:
    """Attribute P&L for long spot and short equal-dollar perpetual legs.

    The initial USD notional is applied to each leg. Fee and slippage inputs are combined
    round-trip costs for the paired position, matching the scanner's cost convention.
    """
    if notional_usd <= 0:
        raise ValueError("notional_usd must be positive")
    for name, value in (
        ("entry_spot_px", entry_spot_px),
        ("exit_spot_px", exit_spot_px),
        ("entry_perp_px", entry_perp_px),
        ("exit_perp_px", exit_perp_px),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be positive")
    if round_trip_fee_bps < 0 or round_trip_slippage_bps < 0:
        raise ValueError("modeled costs cannot be negative")

    spot_quantity = notional_usd / entry_spot_px
    perp_quantity = notional_usd / entry_perp_px
    spot_pnl = spot_quantity * (exit_spot_px - entry_spot_px)
    perp_pnl = -perp_quantity * (exit_perp_px - entry_perp_px)
    fee_cost = notional_usd * round_trip_fee_bps / 10_000.0
    slippage_cost = notional_usd * round_trip_slippage_bps / 10_000.0
    net_pnl = spot_pnl + perp_pnl + funding_pnl_usd - fee_cost - slippage_cost

    return TradeAttribution(
        spot_pnl_usd=spot_pnl,
        perp_pnl_usd=perp_pnl,
        funding_pnl_usd=funding_pnl_usd,
        fee_cost_usd=fee_cost,
        slippage_cost_usd=slippage_cost,
        net_pnl_usd=net_pnl,
    )

