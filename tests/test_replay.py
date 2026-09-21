import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from cleancarry.config import Settings
from cleancarry.research.replay import ReplayConfig, run_hysteresis_replay
from cleancarry.research.studies import (
    StudyConfig,
    load_archived_market_observations,
    run_carry_study,
)


def _signal_row(
    observed_at: str,
    *,
    spot_mid: float,
    perp_mid: float,
    signal_net_apr: float,
    eligible: bool = True,
    funding: float = 0.001,
) -> dict[str, object]:
    return {
        "coin": "BTC",
        "spot_market": "@1",
        "observed_at_utc": observed_at,
        "spot_mid": spot_mid,
        "perp_mid": perp_mid,
        "spot_day_volume_usd": 10_000_000,
        "perp_day_volume_usd": 100_000_000,
        "current_funding_hourly": funding,
        "predicted_funding_hourly": funding,
        "ewma_24h": funding,
        "ewma_72h": funding,
        "funding_vol_72h": 0.00001,
        "basis_bps": 10,
        "spot_spread_bps": 0,
        "perp_spread_bps": 0,
        "signal_net_apr": signal_net_apr,
        "signal_eligible": eligible,
    }


def _market_row(observed_at: str, *, funding: float = 0.0002) -> dict[str, object]:
    row = _signal_row(
        observed_at,
        spot_mid=100,
        perp_mid=100.1,
        signal_net_apr=0,
        funding=funding,
    )
    row.pop("signal_net_apr")
    row.pop("signal_eligible")
    return row


def test_replay_uses_start_of_interval_funding_and_reconciles_pnl():
    observations = pd.DataFrame(
        [
            _signal_row(
                "2026-01-01T00:00:00Z",
                spot_mid=100,
                perp_mid=101,
                signal_net_apr=0.20,
            ),
            _signal_row(
                "2026-01-01T01:00:00Z",
                spot_mid=102,
                perp_mid=102,
                signal_net_apr=0.20,
            ),
            _signal_row(
                "2026-01-01T02:00:00Z",
                spot_mid=103,
                perp_mid=102,
                signal_net_apr=0.04,
            ),
        ]
    )
    result = run_hysteresis_replay(
        observations,
        model="ensemble",
        config=ReplayConfig(
            notional_usd=1_000,
            entry_net_apr=0.10,
            exit_net_apr=0.05,
            round_trip_fee_bps=0,
        ),
    )

    assert result.summary.trade_count == 1
    trade = result.trades[0]
    expected_funding = 1_000 * 0.001 + (1_000 / 101 * 102) * 0.001
    assert trade.funding_pnl_usd == pytest.approx(expected_funding)
    assert trade.net_pnl_usd == pytest.approx(
        trade.spot_pnl_usd
        + trade.perp_pnl_usd
        + trade.funding_pnl_usd
        - trade.fee_cost_usd
        - trade.slippage_cost_usd
    )
    assert trade.exit_reason == "exit_hurdle"


def test_archive_loader_excludes_future_observations(tmp_path):
    derived = tmp_path / "derived"
    derived.mkdir()
    path = derived / "carry_markets_20260101T000000Z.parquet"
    pd.DataFrame(
        [
            _market_row("2026-01-01T00:00:00Z"),
            _market_row("2026-01-01T01:00:00Z"),
            _market_row("2026-01-01T02:00:00Z", funding=9.0),
        ]
    ).to_parquet(path, index=False)

    observations, inputs = load_archived_market_observations(
        derived,
        coin="BTC",
        as_of_utc=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )

    assert len(observations) == 2
    assert observations["observed_at_utc"].max() == pd.Timestamp("2026-01-01T01:00:00Z")
    assert observations["current_funding_hourly"].tolist() == [0.0002, 0.0002]
    assert inputs[0].selected_rows == 2


def test_study_writes_versioned_manifest_and_model_comparison(tmp_path):
    derived = tmp_path / "derived"
    derived.mkdir()
    pd.DataFrame(
        [
            _market_row("2026-01-01T00:00:00Z"),
            _market_row("2026-01-01T01:00:00Z"),
            _market_row("2026-01-01T02:00:00Z", funding=-0.0002),
            _market_row("2026-01-01T03:00:00Z", funding=5.0),
        ]
    ).to_parquet(derived / "carry_markets_fixture.parquet", index=False)
    settings = Settings(
        min_spot_day_volume_usd=1,
        min_perp_day_volume_usd=1,
        min_net_apr=0.10,
        exit_net_apr=0.05,
        expected_hold_hours=168,
        round_trip_fees_bps=0,
        basis_risk_buffer_apr=0,
    )

    result = run_carry_study(
        derived_dir=derived,
        output_root=tmp_path / "studies",
        settings=settings,
        config=StudyConfig(
            coin="btc",
            as_of_utc=datetime(2026, 1, 1, 2, tzinfo=UTC),
            minimum_trades_for_decision=2,
        ),
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["decision"] == "INSUFFICIENT_DATA"
    assert set(manifest["models"]) == {"ensemble", "current_only"}
    assert manifest["data_identity"]["observation_count"] == 3
    assert manifest["data_identity"]["last_observed_at_utc"].startswith(
        "2026-01-01T02:00:00"
    )
    assert (result.output_dir / "trades_ensemble.parquet").exists()
    assert (result.output_dir / "trades_current_only.parquet").exists()
