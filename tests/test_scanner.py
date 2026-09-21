from cleancarry.config import Settings
from cleancarry.models import CarryMarket
from cleancarry.scanner import (
    build_intersection,
    expected_funding_rate,
    parse_predicted_fundings,
    score_market,
)


def test_predicted_fundings_prefers_hyperliquid():
    payload = [["BTC", [["BinPerp", {"fundingRate": "0.001"}], ["HlPerp", {"fundingRate": "0.0002"}]]]]
    assert parse_predicted_fundings(payload)["BTC"] == 0.0002


def test_intersection_maps_usdc_spot_to_perp():
    perp = [
        {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
        [{"midPx": "100", "funding": "0.0001"}, {"midPx": "10", "funding": "0.0"}],
    ]
    spot = [
        {
            "tokens": [
                {"index": 0, "name": "USDC"},
                {"index": 1, "name": "BTC"},
                {"index": 2, "name": "DOGE"},
            ],
            "universe": [
                {"name": "@1", "tokens": [1, 0], "isCanonical": True},
                {"name": "@2", "tokens": [2, 0], "isCanonical": True},
            ],
        },
        [{"midPx": "99"}, {"midPx": "1"}],
    ]
    rows = build_intersection(perp, spot)
    assert [r["coin"] for r in rows] == ["BTC"]
    assert rows[0]["spot_market"] == "@1"


def test_expected_funding_renormalizes_missing_inputs():
    rate = expected_funding_rate(0.0001, None, 0.0002, None)
    assert abs(rate - ((0.0001 * 0.25 + 0.0002 * 0.25) / 0.5)) < 1e-12


def test_score_market_can_be_eligible():
    settings = Settings(
        min_spot_day_volume_usd=1,
        min_perp_day_volume_usd=1,
        min_net_apr=0.01,
        expected_hold_hours=168,
        round_trip_fees_bps=1,
        basis_risk_buffer_apr=0.0,
    )
    market = CarryMarket(
        coin="BTC",
        spot_market="@1",
        spot_mid=100,
        perp_mid=100.1,
        spot_day_volume_usd=10_000_000,
        perp_day_volume_usd=100_000_000,
        current_funding_hourly=0.0001,
        predicted_funding_hourly=0.0001,
        ewma_24h=0.0001,
        ewma_72h=0.0001,
        funding_vol_72h=0.00001,
        basis_bps=10,
        spot_spread_bps=1,
        perp_spread_bps=1,
    )
    out = score_market(market, settings)
    assert out.eligible
    assert out.expected_net_apr > 0


def test_intersection_supports_hypercore_aliases():
    perp = [{"universe": [{"name": "BTC"}]}, [{"midPx": "100", "funding": "0.0001"}]]
    spot = [
        {
            "tokens": [{"index": 0, "name": "USDC"}, {"index": 1, "name": "UBTC"}],
            "universe": [{"name": "@9", "tokens": [1, 0], "isCanonical": True}],
        },
        [{"midPx": "100"}],
    ]
    rows = build_intersection(perp, spot, {"UBTC": "BTC"})
    assert rows[0]["coin"] == "BTC"
    assert rows[0]["spot_token"] == "UBTC"
