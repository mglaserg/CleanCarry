from __future__ import annotations

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

    assert account_equity_usd(perp, spot, {"HYPE": 40}) == 1180


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
