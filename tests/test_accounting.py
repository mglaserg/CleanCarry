import pytest

from cleancarry.research.accounting import attribute_trade


def test_attribute_trade_reconciles_all_components():
    result = attribute_trade(
        notional_usd=1_000,
        entry_spot_px=100,
        exit_spot_px=103,
        entry_perp_px=101,
        exit_perp_px=102,
        funding_pnl_usd=2,
        round_trip_fee_bps=10,
        round_trip_slippage_bps=4,
    )

    assert result.spot_pnl_usd == pytest.approx(30)
    assert result.perp_pnl_usd == pytest.approx(-1_000 / 101)
    assert result.fee_cost_usd == pytest.approx(1)
    assert result.slippage_cost_usd == pytest.approx(0.4)
    assert result.net_pnl_usd == pytest.approx(
        result.spot_pnl_usd
        + result.perp_pnl_usd
        + result.funding_pnl_usd
        - result.fee_cost_usd
        - result.slippage_cost_usd
    )


def test_attribute_trade_rejects_nonpositive_notional():
    with pytest.raises(ValueError, match="notional_usd"):
        attribute_trade(
            notional_usd=0,
            entry_spot_px=100,
            exit_spot_px=100,
            entry_perp_px=100,
            exit_perp_px=100,
            funding_pnl_usd=0,
            round_trip_fee_bps=0,
            round_trip_slippage_bps=0,
        )

