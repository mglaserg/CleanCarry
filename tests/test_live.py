from __future__ import annotations

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from cleancarry import cli
from cleancarry.cli import app
from cleancarry.config import Settings
from cleancarry.live import Fill, LivePairExecutor, LiveState, LiveStateStore, account_equity_usd


class _Venue:
    def __init__(self, *, perp_failure: bool = False) -> None:
        self.perp_failure = perp_failure
        self.calls: list[tuple] = []

    def market_open(self, market, is_buy, quantity, action_id):
        self.calls.append(("market", market, is_buy, quantity, action_id))
        return Fill(quantity, 10.0, 1)

    def market_close_perp(self, coin, quantity, action_id):
        self.calls.append(("close", coin, quantity, action_id))
        return Fill(quantity, 10.0, 2)


def test_live_pair_entry_and_exit_are_persisted(tmp_path):
    store = LiveStateStore(tmp_path)
    state = LiveState(status="ARMED")
    venue = _Venue()
    executor = LivePairExecutor(Settings(), store, venue)

    position = executor.enter(
        state,
        coin="HYPE",
        spot_market="@107",
        spot_token="HYPE",
        quantity=2,
        target_notional_usd=20,
        action_id="entry-1",
    )

    assert position.quantity == 2
    assert store.load().positions["HYPE"].quantity == 2
    executor.exit(state, "HYPE", "exit-1")
    assert store.load().positions == {}
    assert [call[0] for call in venue.calls] == ["market", "market", "market", "close"]


def test_live_entry_flattens_spot_when_perp_leg_fails(tmp_path):
    class FailingVenue(_Venue):
        def market_open(self, market, is_buy, quantity, action_id):
            if market == "HYPE":
                raise RuntimeError("perp rejected")
            return super().market_open(market, is_buy, quantity, action_id)

    store = LiveStateStore(tmp_path)
    state = LiveState(status="ARMED")
    venue = FailingVenue()
    executor = LivePairExecutor(Settings(), store, venue)

    try:
        executor.enter(
            state,
            coin="HYPE",
            spot_market="@107",
            spot_token="HYPE",
            quantity=2,
            target_notional_usd=20,
            action_id="entry-fail",
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the rejected perpetual leg to fail the pair")

    assert state.status == "SAFE_MODE"
    assert [call[2] for call in venue.calls] == [True, False]


def test_account_equity_includes_marked_spot_for_compounding():
    perp = {"marginSummary": {"accountValue": "1000"}}
    spot = {"balances": [{"coin": "USDC", "total": "100"}, {"coin": "HYPE", "total": "2"}]}

    assert account_equity_usd(perp, spot, {"HYPE": 40}, account_mode="disabled") == 1180


def test_unified_equity_uses_spot_balances_and_perp_pnl_without_legacy_account_value():
    perp = {
        "marginSummary": {"accountValue": "1000"},
        "assetPositions": [{"position": {"unrealizedPnl": "10"}}],
    }
    spot = {"balances": [{"coin": "USDC", "total": "100"}, {"coin": "HYPE", "total": "2"}]}

    assert account_equity_usd(perp, spot, {"HYPE": 40}, account_mode="unifiedAccount") == 190


def test_unified_equity_fails_closed_on_unknown_mode_or_unpriced_spot():
    perp = {"marginSummary": {"accountValue": "1000"}, "assetPositions": []}
    spot = {"balances": [{"coin": "USDC", "total": "100"}, {"coin": "HYPE", "total": "2"}]}

    with pytest.raises(ValueError, match="unsupported or ambiguous"):
        account_equity_usd(perp, spot, {"HYPE": 40}, account_mode="default")
    with pytest.raises(ValueError, match="missing valid spot price"):
        account_equity_usd(perp, spot, {}, account_mode="unifiedAccount")


def test_live_stop_survives_running_process_state_save_and_blocks_new_order(tmp_path):
    store = LiveStateStore(tmp_path)
    state = LiveState(status="ARMED")
    store.save(state)
    store.request_stop()
    store.save(state)  # A running process may finish a cycle after the operator stops it.

    assert store.load().status == "SAFE_MODE"
    venue = _Venue()
    executor = LivePairExecutor(Settings(), store, venue)
    with pytest.raises(RuntimeError, match="live stop requested"):
        executor.enter(
            state,
            coin="HYPE",
            spot_market="@107",
            spot_token="HYPE",
            quantity=1,
            target_notional_usd=10,
            action_id="blocked-entry",
        )
    assert venue.calls == []
    store.clear_stop()
    assert store.load().status == "ARMED"


def test_live_control_safe_uses_separate_stop_marker(tmp_path, monkeypatch):
    store = LiveStateStore(tmp_path)
    store.save(LiveState(status="ARMED"))
    state_before = store.path.read_text(encoding="utf-8")
    monkeypatch.setattr("cleancarry.cli._settings", lambda: Settings(state_dir=tmp_path))

    result = CliRunner().invoke(app, ["live-control", "safe"])

    assert result.exit_code == 0
    assert store.stop_requested()
    assert store.load().status == "SAFE_MODE"
    assert store.path.read_text(encoding="utf-8") == state_before


def test_live_cycle_reads_unified_mode_without_double_counting(tmp_path, monkeypatch):
    class InfoClient:
        def __init__(self, _base_url):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def user_abstraction(self, _address):
            return "unifiedAccount"

        def clearinghouse_state(self, _address):
            return {"marginSummary": {"accountValue": "1000"}, "assetPositions": []}

        def spot_clearinghouse_state(self, _address):
            return {"balances": [{"coin": "USDC", "total": "1000"}]}

    monkeypatch.setattr(cli, "HyperliquidInfoClient", InfoClient)
    monkeypatch.setattr(cli, "scan", lambda _client, _settings: ([], [], {}))
    monkeypatch.setattr(cli, "_archive_scan", lambda *_args: None)
    store = LiveStateStore(tmp_path)
    state = LiveState(status="ARMED")
    settings = Settings(state_dir=tmp_path, data_dir=tmp_path)

    cli._run_live_cycle(settings, store, state, object(), "0xaccount", datetime.now(UTC))

    assert state.last_equity_usd == 1000
    assert store.load().last_equity_usd == 1000


def test_live_reduction_recovers_a_partial_perp_close(tmp_path):
    class PartialCloseVenue(_Venue):
        def market_close_perp(self, coin, quantity, action_id):
            self.calls.append(("close", coin, quantity, action_id))
            return Fill(quantity / 2, 10.0, 2)

    store = LiveStateStore(tmp_path)
    state = LiveState(status="ARMED")
    venue = PartialCloseVenue()
    executor = LivePairExecutor(Settings(), store, venue)
    executor.enter(
        state,
        coin="HYPE",
        spot_market="@107",
        spot_token="HYPE",
        quantity=2,
        target_notional_usd=20,
        action_id="entry-partial-exit",
    )

    remaining = executor.reduce(
        state,
        coin="HYPE",
        quantity=2,
        target_notional_usd=10,
        action_id="reduce-partial",
    )

    assert remaining is not None
    assert remaining.quantity == 1
    assert venue.calls[-1][0:3] == ("market", "@107", True)
