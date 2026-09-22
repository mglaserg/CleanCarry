from datetime import UTC, datetime, timedelta

import pytest

from cleancarry.config import Settings
from cleancarry.models import Opportunity, RejectionCode
from cleancarry.paper import (
    AutonomousPaperStrategy,
    FillPlan,
    PaperState,
    PaperStateStore,
    ShadowPairExecutor,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _settings(**overrides):
    values = {
        "min_net_apr": 0.10,
        "exit_net_apr": 0.05,
        "strategy_capital_usd": 5_000,
        "max_total_deployment_usd": 4_000,
        "max_per_asset_usd": 1_000,
        "max_positions": 1,
        "perp_margin_fraction": 0.2,
        "max_margin_utilization": 0.5,
        "hedge_tolerance_bps": 50,
        "trade_buffer_usd": 1,
    }
    values.update(overrides)
    return Settings(**values)


def _opportunity(
    coin: str,
    net_apr: float,
    *,
    eligible: bool | None = None,
    reasons: tuple[str, ...] = (),
    observed_at: datetime | None = None,
    capacity: float = 10_000,
):
    if eligible is None:
        eligible = not reasons and net_apr >= 0.10
    return Opportunity(
        coin=coin,
        spot_market=f"@{coin}",
        expected_funding_hourly=net_apr / 8760,
        gross_funding_apr=net_apr + 0.02,
        estimated_round_trip_cost_bps=2,
        cost_apr_at_expected_hold=0.01,
        basis_risk_buffer_apr=0.01,
        expected_net_apr=net_apr,
        basis_bps=5,
        current_funding_apr=net_apr,
        predicted_funding_apr=net_apr,
        spot_day_volume_usd=1_000_000,
        perp_day_volume_usd=2_000_000,
        spot_spread_bps=1,
        perp_spread_bps=1,
        eligible=eligible,
        reason="ELIGIBLE" if not reasons else ",".join(reasons),
        rejection_codes=reasons,
        capacity_usd=capacity,
        observed_at_utc=observed_at.isoformat() if observed_at else None,
    )


def test_autonomous_lifecycle_holds_exits_redeploys_and_restarts(tmp_path):
    settings = _settings()
    store = PaperStateStore(tmp_path)
    strategy = AutonomousPaperStrategy(settings, store)
    prices = {"AAA": (100, 100), "BBB": (50, 50)}

    first = strategy.run_cycle(
        [_opportunity("AAA", 0.20), _opportunity("BBB", 0.15)],
        prices,
        now=NOW,
    )
    assert first.active_coins == ("AAA",)
    assert first.selected[0].coin == "AAA"

    hold = strategy.run_cycle(
        [
            _opportunity(
                "AAA",
                0.07,
                eligible=False,
                reasons=(RejectionCode.EXPECTED_NET_CARRY_TOO_LOW,),
            ),
            _opportunity("BBB", 0.15),
        ],
        prices,
        now=NOW + timedelta(minutes=5),
    )
    assert hold.active_coins == ("AAA",)
    assert not [action for action in hold.actions if action.action == "EXIT"]

    replaced = strategy.run_cycle(
        [
            _opportunity(
                "AAA",
                0.01,
                eligible=False,
                reasons=(RejectionCode.EXPECTED_NET_CARRY_TOO_LOW,),
            ),
            _opportunity("BBB", 0.15),
        ],
        prices,
        now=NOW + timedelta(minutes=10),
    )
    assert [action.action for action in replaced.actions] == ["EXIT", "ENTER"]
    assert replaced.active_coins == ("BBB",)

    restarted = AutonomousPaperStrategy(settings, PaperStateStore(tmp_path))
    after_restart = restarted.run_cycle(
        [_opportunity("BBB", 0.15)],
        prices,
        now=NOW + timedelta(minutes=15),
    )
    assert after_restart.active_coins == ("BBB",)
    assert not [action for action in after_restart.actions if action.action == "ENTER"]


@pytest.mark.parametrize(
    ("plan", "status"),
    [
        (FillPlan(perp_rejected=True), "FAILED_RECOVERED"),
        (FillPlan(spot_rejected=True), "FAILED_RECOVERED"),
        (FillPlan(spot_fill_ratio=0.5, perp_fill_ratio=0.5), "PARTIAL_HEDGED"),
    ],
)
def test_paired_execution_handles_one_leg_and_partial_fills(tmp_path, plan, status):
    settings = _settings()
    store = PaperStateStore(tmp_path)
    executor = ShadowPairExecutor(settings, store)
    state = PaperState()
    result = executor.enter(
        state,
        _opportunity("AAA", 0.20),
        spot_price=100,
        perp_price=100,
        notional_usd=1_000,
        action_id="entry-1",
        now=NOW,
        fill_plan=plan,
    )
    assert result.status == status
    assert ("AAA" in state.positions) is (status == "PARTIAL_HEDGED")


def test_unrecoverable_leg_failure_enters_safe_mode_and_duplicate_is_noop(tmp_path):
    settings = _settings()
    store = PaperStateStore(tmp_path)
    executor = ShadowPairExecutor(settings, store)
    state = PaperState()
    opportunity = _opportunity("AAA", 0.20)
    first = executor.enter(
        state,
        opportunity,
        spot_price=100,
        perp_price=100,
        notional_usd=1_000,
        action_id="entry-1",
        now=NOW,
        fill_plan=FillPlan(perp_rejected=True, recovery_succeeds=False),
    )
    duplicate = executor.enter(
        state,
        opportunity,
        spot_price=100,
        perp_price=100,
        notional_usd=1_000,
        action_id="entry-1",
        now=NOW,
    )
    assert first.status == "UNRESOLVED_EXPOSURE"
    assert state.status == "SAFE_MODE"
    assert duplicate.status == "DUPLICATE_NOOP"

    store.save(state)
    restarted = AutonomousPaperStrategy(settings, PaperStateStore(tmp_path))
    report = restarted.run_cycle(
        [_opportunity("BBB", 0.30)],
        {"BBB": (100, 100)},
        now=NOW + timedelta(minutes=5),
    )
    assert report.status == "SAFE_MODE"
    assert report.active_coins == ()


def test_hedge_buffer_and_reconciliation(tmp_path):
    settings = _settings(hedge_tolerance_bps=50, trade_buffer_usd=20)
    store = PaperStateStore(tmp_path)
    strategy = AutonomousPaperStrategy(settings, store)
    strategy.run_cycle([_opportunity("AAA", 0.20)], {"AAA": (100, 100)}, now=NOW)

    tiny = strategy.run_cycle(
        [_opportunity("AAA", 0.20)],
        {"AAA": (100.1, 100)},
        now=NOW + timedelta(minutes=5),
    )
    assert not [action for action in tiny.actions if action.action == "REBALANCE"]

    large = strategy.run_cycle(
        [_opportunity("AAA", 0.20)],
        {"AAA": (110, 100)},
        now=NOW + timedelta(minutes=10),
    )
    assert [action.status for action in large.actions if action.action == "REBALANCE"] == ["FILLED"]

    position = store.load().positions["AAA"]
    assert strategy.reconcile(
        {"AAA": (position.spot_quantity, position.perp_quantity)},
        now=NOW + timedelta(minutes=11),
    ) == []
    issues = strategy.reconcile(
        {"AAA": (position.spot_quantity, position.perp_quantity + 1)},
        now=NOW + timedelta(minutes=12),
    )
    assert issues == ["PERP_MISMATCH:AAA"]
    assert store.load().status == "SAFE_MODE"


def test_target_resize_moves_to_percentage_buffer_edge(tmp_path):
    settings = _settings(
        max_positions=1,
        max_total_deployment_usd=1_000,
        max_per_asset_usd=1_000,
        trade_buffer_fraction=0.04,
        trade_buffer_usd=1,
    )
    store = PaperStateStore(tmp_path)
    strategy = AutonomousPaperStrategy(settings, store)
    strategy.run_cycle([_opportunity("AAA", 0.20)], {"AAA": (100, 100)}, now=NOW)
    state = store.load()
    state.positions["AAA"].spot_quantity = 5
    state.positions["AAA"].perp_quantity = 5
    store.save(state)

    report = strategy.run_cycle(
        [_opportunity("AAA", 0.20)],
        {"AAA": (100, 100)},
        now=NOW + timedelta(minutes=5),
    )

    resize = next(action for action in report.actions if action.action == "RESIZE")
    assert "buffer edge" in resize.detail
    # Capital/margin limit is $1,000 and a 4% full buffer has a $980 lower edge.
    assert store.load().positions["AAA"].spot_notional(100) == pytest.approx(980)


def test_stale_data_and_capital_limits_prevent_entries(tmp_path):
    settings = _settings(
        stale_data_seconds=60,
        max_positions=3,
        max_total_deployment_usd=1_500,
        max_per_asset_usd=1_000,
    )
    strategy = AutonomousPaperStrategy(settings, PaperStateStore(tmp_path))
    report = strategy.run_cycle(
        [
            _opportunity("STALE", 0.30, observed_at=NOW - timedelta(minutes=2)),
            _opportunity("AAA", 0.20),
            _opportunity("BBB", 0.15),
            _opportunity("TINY", 0.50, capacity=0.5),
        ],
        {coin: (100, 100) for coin in ("STALE", "AAA", "BBB", "TINY")},
        now=NOW,
    )
    assert report.active_coins == ("AAA", "BBB")
    # Fixed slot caps leave unused cash rather than concentrating into too few names.
    assert report.deployed_notional_usd == pytest.approx(1_000)
    assert report.rejection_counts[RejectionCode.STALE_DATA] == 1


def test_required_exit_without_valid_prices_enters_safe_mode(tmp_path):
    strategy = AutonomousPaperStrategy(_settings(), PaperStateStore(tmp_path))
    strategy.run_cycle([_opportunity("AAA", 0.20)], {"AAA": (100, 100)}, now=NOW)
    report = strategy.run_cycle(
        [
            _opportunity(
                "AAA",
                0.01,
                eligible=False,
                reasons=(RejectionCode.FUNDING_TOO_LOW,),
            )
        ],
        {},
        now=NOW + timedelta(minutes=5),
    )
    assert report.status == "SAFE_MODE"
    assert report.actions[0].status == "BLOCKED_NO_PRICE"
    assert report.active_coins == ("AAA",)


def test_invalid_hysteresis_configuration_fails_closed():
    with pytest.raises(ValueError, match="below"):
        Settings(min_net_apr=0.05, exit_net_apr=0.05)


def test_read_only_generates_intentions_without_persisting_or_filling(tmp_path):
    store = PaperStateStore(tmp_path)
    strategy = AutonomousPaperStrategy(_settings(), store)
    report = strategy.run_cycle(
        [_opportunity("AAA", 0.20)],
        {"AAA": (100, 100)},
        now=NOW,
        mode="read_only",
    )
    assert report.selected[0].coin == "AAA"
    assert report.actions == ()
    assert not store.state_path.exists()


def test_live_mode_is_hard_blocked(tmp_path):
    strategy = AutonomousPaperStrategy(_settings(), PaperStateStore(tmp_path))
    with pytest.raises(ValueError, match="live execution is not implemented"):
        strategy.run_cycle([], {}, now=NOW, mode="live")  # type: ignore[arg-type]
