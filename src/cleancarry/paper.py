from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .config import Settings
from .models import Opportunity, RejectionCode

PAPER_STATE_SCHEMA_VERSION = 1
ExecutionMode = Literal["read_only", "shadow"]


@dataclass(frozen=True)
class FillPlan:
    """Deterministic shadow fill behavior used by the simulator and failure tests."""

    spot_fill_ratio: float = 1.0
    perp_fill_ratio: float = 1.0
    spot_rejected: bool = False
    perp_rejected: bool = False
    recovery_succeeds: bool = True

    def __post_init__(self) -> None:
        for value in (self.spot_fill_ratio, self.perp_fill_ratio):
            if not 0 <= value <= 1:
                raise ValueError("fill ratios must be between zero and one")


@dataclass
class PaperPosition:
    coin: str
    spot_market: str
    spot_quantity: float
    perp_quantity: float
    entry_spot_px: float
    entry_perp_px: float
    opened_at_utc: str
    updated_at_utc: str
    entry_expected_net_apr: float
    funding_accrued_usd: float = 0.0
    last_spot_px: float | None = None
    last_perp_px: float | None = None
    hedge_error_bps: float = 0.0
    current_expected_net_apr: float | None = None

    def spot_notional(self, price: float) -> float:
        return self.spot_quantity * price

    def perp_notional(self, price: float) -> float:
        return self.perp_quantity * price


@dataclass
class PaperState:
    schema_version: int = PAPER_STATE_SCHEMA_VERSION
    mode: str = "shadow"
    status: str = "RUNNING"
    cycle_count: int = 0
    last_cycle_at_utc: str | None = None
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    completed_action_ids: list[str] = field(default_factory=list)
    unresolved_exposure: dict[str, dict[str, float]] = field(default_factory=dict)
    close_requests: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionResult:
    action_id: str
    coin: str
    action: str
    status: str
    spot_filled_notional_usd: float
    perp_filled_notional_usd: float
    hedge_error_bps: float
    detail: str


@dataclass(frozen=True)
class Selection:
    coin: str
    rank: int
    notional_usd: float
    expected_net_apr: float
    reason: str


@dataclass(frozen=True)
class CycleReport:
    cycle_id: str
    mode: str
    status: str
    discovered: int
    eligible: int
    selected: tuple[Selection, ...]
    active_coins: tuple[str, ...]
    actions: tuple[ExecutionResult, ...]
    deployed_notional_usd: float
    free_strategy_capital_usd: float
    margin_utilization: float
    rejection_counts: dict[str, int]
    last_cycle_at_utc: str

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


class PaperStateStore:
    """Atomic paper state plus append-only decision/execution audit events."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_path = state_dir / "paper_state.json"
        self.ledger_path = state_dir / "paper_ledger.jsonl"

    def load(self) -> PaperState:
        if not self.state_path.exists():
            return PaperState()
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != PAPER_STATE_SCHEMA_VERSION:
            raise ValueError("unsupported paper state schema version")
        payload["positions"] = {
            coin: PaperPosition(**position)
            for coin, position in payload.get("positions", {}).items()
        }
        return PaperState(**payload)

    def save(self, state: PaperState) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = asdict(state)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.state_dir,
            prefix=".paper-state-",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            temporary = Path(handle.name)
        try:
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def append_event(self, event: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class ShadowPairExecutor:
    """Paired-leg simulator with idempotency and a flatten-on-failure safety policy."""

    def __init__(self, settings: Settings, store: PaperStateStore) -> None:
        self.settings = settings
        self.store = store

    def enter(
        self,
        state: PaperState,
        opportunity: Opportunity,
        *,
        spot_price: float,
        perp_price: float,
        notional_usd: float,
        action_id: str,
        now: datetime,
        fill_plan: FillPlan | None = None,
    ) -> ExecutionResult:
        fill_plan = fill_plan or FillPlan()
        duplicate = self._duplicate(state, action_id, opportunity.coin, "ENTER")
        if duplicate is not None:
            return duplicate
        spot_filled = 0.0 if fill_plan.spot_rejected else notional_usd * fill_plan.spot_fill_ratio
        perp_filled = 0.0 if fill_plan.perp_rejected else notional_usd * fill_plan.perp_fill_ratio
        hedge_error = _hedge_error_bps(spot_filled, perp_filled)
        timestamp = _utc(now)

        if spot_filled > 0 and perp_filled > 0 and hedge_error <= self.settings.hedge_tolerance_bps:
            state.positions[opportunity.coin] = PaperPosition(
                coin=opportunity.coin,
                spot_market=opportunity.spot_market,
                spot_quantity=spot_filled / spot_price,
                perp_quantity=perp_filled / perp_price,
                entry_spot_px=spot_price,
                entry_perp_px=perp_price,
                opened_at_utc=timestamp,
                updated_at_utc=timestamp,
                entry_expected_net_apr=opportunity.expected_net_apr,
                last_spot_px=spot_price,
                last_perp_px=perp_price,
                current_expected_net_apr=opportunity.expected_net_apr,
            )
            status = (
                "FILLED"
                if math.isclose(spot_filled, notional_usd)
                and math.isclose(perp_filled, notional_usd)
                else "PARTIAL_HEDGED"
            )
            detail = "both simulated legs verified within hedge tolerance"
        elif fill_plan.recovery_succeeds:
            status = "FAILED_RECOVERED"
            detail = "unmatched simulated fills were immediately flattened"
        else:
            status = "UNRESOLVED_EXPOSURE"
            detail = "paired entry failed and flatten recovery failed; strategy entered SAFE_MODE"
            state.status = "SAFE_MODE"
            state.unresolved_exposure[opportunity.coin] = {
                "spot_notional_usd": spot_filled,
                "perp_notional_usd": perp_filled,
            }

        result = ExecutionResult(
            action_id=action_id,
            coin=opportunity.coin,
            action="ENTER",
            status=status,
            spot_filled_notional_usd=spot_filled,
            perp_filled_notional_usd=perp_filled,
            hedge_error_bps=hedge_error,
            detail=detail,
        )
        self._complete(state, result, timestamp)
        return result

    def exit(
        self,
        state: PaperState,
        *,
        coin: str,
        spot_price: float,
        perp_price: float,
        action_id: str,
        reason: str,
        now: datetime,
    ) -> ExecutionResult:
        duplicate = self._duplicate(state, action_id, coin, "EXIT")
        if duplicate is not None:
            return duplicate
        position = state.positions.get(coin)
        if position is None:
            result = ExecutionResult(action_id, coin, "EXIT", "NO_POSITION", 0, 0, 0, reason)
        else:
            spot_notional = position.spot_notional(spot_price)
            perp_notional = position.perp_notional(perp_price)
            result = ExecutionResult(
                action_id,
                coin,
                "EXIT",
                "FILLED",
                spot_notional,
                perp_notional,
                _hedge_error_bps(spot_notional, perp_notional),
                reason,
            )
            del state.positions[coin]
        self._complete(state, result, _utc(now))
        return result

    def rebalance(
        self,
        state: PaperState,
        *,
        coin: str,
        spot_price: float,
        perp_price: float,
        action_id: str,
        now: datetime,
    ) -> ExecutionResult | None:
        position = state.positions[coin]
        spot_notional = position.spot_notional(spot_price)
        perp_notional = position.perp_notional(perp_price)
        difference = spot_notional - perp_notional
        hedge_error = _hedge_error_bps(spot_notional, perp_notional)
        if (
            abs(difference) < self.settings.trade_buffer_usd
            or hedge_error <= self.settings.hedge_tolerance_bps
        ):
            return None
        if difference > 0:
            position.perp_quantity = spot_notional / perp_price
        else:
            position.spot_quantity = perp_notional / spot_price
        position.updated_at_utc = _utc(now)
        position.last_spot_px = spot_price
        position.last_perp_px = perp_price
        position.hedge_error_bps = 0.0
        matched = max(spot_notional, perp_notional)
        result = ExecutionResult(
            action_id,
            coin,
            "REBALANCE",
            "FILLED",
            matched,
            matched,
            0.0,
            "hedge error exceeded configured tolerance and trade buffer",
        )
        self._complete(state, result, _utc(now))
        return result

    def resize_to_buffer_edge(
        self,
        state: PaperState,
        *,
        coin: str,
        spot_price: float,
        perp_price: float,
        target_notional_usd: float,
        action_id: str,
        now: datetime,
    ) -> ExecutionResult | None:
        """Move both legs to the edge of the RobotWealth percentage no-trade region."""
        position = state.positions[coin]
        current = (
            position.spot_notional(spot_price) + position.perp_notional(perp_price)
        ) / 2.0
        half_buffer = target_notional_usd * self.settings.trade_buffer_fraction / 2.0
        lower = max(target_notional_usd - half_buffer, 0.0)
        upper = target_notional_usd + half_buffer
        if lower <= current <= upper:
            return None
        resized = lower if current < lower else upper
        traded = abs(resized - current)
        if traded < self.settings.trade_buffer_usd:
            return None
        position.spot_quantity = resized / spot_price
        position.perp_quantity = resized / perp_price
        position.updated_at_utc = _utc(now)
        position.last_spot_px = spot_price
        position.last_perp_px = perp_price
        position.hedge_error_bps = 0.0
        result = ExecutionResult(
            action_id,
            coin,
            "RESIZE",
            "FILLED",
            traded,
            traded,
            0.0,
            (
                f"target {target_notional_usd:.2f}; resized to no-trade buffer edge "
                f"{resized:.2f}"
            ),
        )
        self._complete(state, result, _utc(now))
        return result

    def _duplicate(
        self, state: PaperState, action_id: str, coin: str, action: str
    ) -> ExecutionResult | None:
        if action_id not in state.completed_action_ids:
            return None
        return ExecutionResult(
            action_id, coin, action, "DUPLICATE_NOOP", 0, 0, 0, "action already completed"
        )

    def _complete(self, state: PaperState, result: ExecutionResult, timestamp: str) -> None:
        state.completed_action_ids.append(result.action_id)
        self.store.append_event({"at_utc": timestamp, **asdict(result)})


class AutonomousPaperStrategy:
    """One deterministic discover/filter/select/execute/monitor/redeploy cycle."""

    def __init__(self, settings: Settings, store: PaperStateStore) -> None:
        self.settings = settings
        self.store = store
        self.executor = ShadowPairExecutor(settings, store)

    def run_cycle(
        self,
        opportunities: list[Opportunity],
        prices: dict[str, tuple[float, float]],
        *,
        now: datetime | None = None,
        mode: ExecutionMode = "shadow",
        fill_plans: dict[str, FillPlan] | None = None,
        discovered_count: int | None = None,
    ) -> CycleReport:
        if mode not in ("read_only", "shadow"):
            raise ValueError("live execution is not implemented; use read_only or shadow")
        now = now or datetime.now(UTC)
        timestamp = _utc(now)
        cycle_id = f"cycle-{timestamp}"
        fill_plans = fill_plans or {}
        state = self.store.load()
        actions: list[ExecutionResult] = []
        selected: list[Selection] = []
        fresh = {item.coin: self._with_freshness(item, now) for item in opportunities}

        if state.unresolved_exposure:
            state.status = "SAFE_MODE"

        # Exit first so released paper capital can be selected again in this same cycle.
        for coin in sorted(state.positions):
            opportunity = fresh.get(coin)
            reason = (
                "OPERATOR_REQUEST"
                if coin in state.close_requests
                else self._exit_reason(opportunity)
            )
            if reason is not None and coin in prices and mode == "shadow":
                spot_price, perp_price = prices[coin]
                actions.append(
                    self.executor.exit(
                        state,
                        coin=coin,
                        spot_price=spot_price,
                        perp_price=perp_price,
                        action_id=f"{cycle_id}:EXIT:{coin}",
                        reason=reason,
                        now=now,
                    )
                )
                if coin in state.close_requests:
                    state.close_requests.remove(coin)
            elif reason is not None and coin not in prices and mode == "shadow":
                state.status = "SAFE_MODE"
                position = state.positions[coin]
                state.unresolved_exposure[coin] = {
                    "spot_quantity": position.spot_quantity,
                    "perp_quantity": position.perp_quantity,
                }
                result = ExecutionResult(
                    action_id=f"{cycle_id}:EXIT:{coin}",
                    coin=coin,
                    action="EXIT",
                    status="BLOCKED_NO_PRICE",
                    spot_filled_notional_usd=0,
                    perp_filled_notional_usd=0,
                    hedge_error_bps=position.hedge_error_bps,
                    detail=f"{reason}; no valid prices for safe paired close",
                )
                actions.append(result)
                self.store.append_event({"at_utc": timestamp, **asdict(result)})

        deployment_limit = self._deployment_limit()
        slot_notional = deployment_limit / self.settings.max_positions

        # Monitor actual simulated notionals and correct only material hedge errors.
        if mode == "shadow":
            for coin in sorted(state.positions):
                if coin not in prices:
                    continue
                spot_price, perp_price = prices[coin]
                position = state.positions[coin]
                position.last_spot_px = spot_price
                position.last_perp_px = perp_price
                position.hedge_error_bps = _hedge_error_bps(
                    position.spot_notional(spot_price), position.perp_notional(perp_price)
                )
                opportunity = fresh.get(coin)
                if opportunity is not None:
                    position.current_expected_net_apr = opportunity.expected_net_apr
                result = self.executor.rebalance(
                    state,
                    coin=coin,
                    spot_price=spot_price,
                    perp_price=perp_price,
                    action_id=f"{cycle_id}:REBALANCE:{coin}",
                    now=now,
                )
                if result is not None:
                    actions.append(result)
                if opportunity is not None:
                    target = min(
                        slot_notional,
                        self.settings.max_per_asset_usd,
                        opportunity.capacity_usd,
                    )
                    resize = self.executor.resize_to_buffer_edge(
                        state,
                        coin=coin,
                        spot_price=spot_price,
                        perp_price=perp_price,
                        target_notional_usd=target,
                        action_id=f"{cycle_id}:RESIZE:{coin}",
                        now=now,
                    )
                    if resize is not None:
                        actions.append(resize)

        ranked = sorted(
            (
                item
                for item in fresh.values()
                if item.eligible and item.coin not in state.positions
            ),
            key=lambda item: (-item.expected_net_apr, item.coin),
        )
        current_deployment = self._deployment(state, prices)
        remaining = max(deployment_limit - current_deployment, 0.0)
        slots = max(self.settings.max_positions - len(state.positions), 0)

        if state.status == "RUNNING":
            for rank, opportunity in enumerate(ranked, start=1):
                if len(selected) >= slots or remaining < self.settings.trade_buffer_usd:
                    break
                notional = min(
                    slot_notional,
                    self.settings.max_per_asset_usd,
                    opportunity.capacity_usd,
                    remaining,
                )
                if notional < self.settings.trade_buffer_usd:
                    continue
                selection = Selection(
                    coin=opportunity.coin,
                    rank=rank,
                    notional_usd=notional,
                    expected_net_apr=opportunity.expected_net_apr,
                    reason=(
                        f"rank {rank}; net APR {opportunity.expected_net_apr:.6f}; "
                        f"capacity {opportunity.capacity_usd:.2f}"
                    ),
                )
                selected.append(selection)
                remaining -= notional
                if mode == "shadow" and opportunity.coin in prices:
                    spot_price, perp_price = prices[opportunity.coin]
                    actions.append(
                        self.executor.enter(
                            state,
                            opportunity,
                            spot_price=spot_price,
                            perp_price=perp_price,
                            notional_usd=notional,
                            action_id=f"{cycle_id}:ENTER:{opportunity.coin}",
                            now=now,
                            fill_plan=fill_plans.get(opportunity.coin, FillPlan()),
                        )
                    )

        if mode == "shadow":
            state.mode = mode
            state.cycle_count += 1
            state.last_cycle_at_utc = timestamp
            self.store.append_event(
                {
                    "at_utc": timestamp,
                    "action": "CYCLE",
                    "cycle_id": cycle_id,
                    "selected": [asdict(item) for item in selected],
                    "status": state.status,
                }
            )
            self.store.save(state)

        deployed = self._deployment(state, prices)
        rejection_counts: dict[str, int] = {}
        for opportunity in fresh.values():
            for reason in opportunity.rejection_codes:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        return CycleReport(
            cycle_id=cycle_id,
            mode=mode,
            status=state.status,
            discovered=discovered_count if discovered_count is not None else len(opportunities),
            eligible=sum(item.eligible for item in fresh.values()),
            selected=tuple(selected),
            active_coins=tuple(sorted(state.positions)),
            actions=tuple(actions),
            deployed_notional_usd=deployed,
            free_strategy_capital_usd=max(
                self.settings.strategy_capital_usd
                - deployed * (1.0 + self.settings.perp_margin_fraction),
                0.0,
            ),
            margin_utilization=(
                deployed * self.settings.perp_margin_fraction
                / self.settings.strategy_capital_usd
            ),
            rejection_counts=rejection_counts,
            last_cycle_at_utc=timestamp,
        )

    def reconcile(
        self,
        observed: dict[str, tuple[float, float]],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """Compare persisted managed quantities with independently observed shadow quantities."""
        now = now or datetime.now(UTC)
        state = self.store.load()
        issues: list[str] = []
        for coin, position in state.positions.items():
            actual = observed.get(coin)
            if actual is None:
                issues.append(f"MISSING_POSITION:{coin}")
                continue
            spot_quantity, perp_quantity = actual
            if not math.isclose(spot_quantity, position.spot_quantity, rel_tol=1e-9):
                issues.append(f"SPOT_MISMATCH:{coin}")
            if not math.isclose(perp_quantity, position.perp_quantity, rel_tol=1e-9):
                issues.append(f"PERP_MISMATCH:{coin}")
        for coin in observed.keys() - state.positions.keys():
            issues.append(f"UNEXPECTED_POSITION:{coin}")
        if issues:
            state.status = "SAFE_MODE"
            self.store.append_event(
                {"at_utc": _utc(now), "action": "RECONCILIATION_FAILED", "issues": issues}
            )
            self.store.save(state)
        return issues

    def _with_freshness(self, opportunity: Opportunity, now: datetime) -> Opportunity:
        if opportunity.observed_at_utc is None:
            return opportunity
        observed = datetime.fromisoformat(opportunity.observed_at_utc)
        age = (now.astimezone(UTC) - observed.astimezone(UTC)).total_seconds()
        if age <= self.settings.stale_data_seconds:
            return opportunity
        reasons = tuple(dict.fromkeys((*opportunity.rejection_codes, RejectionCode.STALE_DATA)))
        return Opportunity(**{**asdict(opportunity), "eligible": False, "reason": ",".join(reasons), "rejection_codes": reasons})

    def _exit_reason(self, opportunity: Opportunity | None) -> str | None:
        if opportunity is None:
            return "MARKET_MISSING"
        structural = set(opportunity.rejection_codes) - {RejectionCode.EXPECTED_NET_CARRY_TOO_LOW}
        if structural:
            return min(structural)
        if opportunity.expected_net_apr < self.settings.exit_net_apr:
            return "EXIT_HURDLE"
        return None

    @staticmethod
    def _deployment(state: PaperState, prices: dict[str, tuple[float, float]]) -> float:
        total = 0.0
        for coin, position in state.positions.items():
            spot_price = prices.get(coin, (position.entry_spot_px, position.entry_perp_px))[0]
            total += position.spot_notional(spot_price)
        return total

    def _deployment_limit(self) -> float:
        return min(
            self.settings.max_total_deployment_usd,
            self.settings.strategy_capital_usd
            / (1.0 + self.settings.perp_margin_fraction),
            self.settings.strategy_capital_usd
            * self.settings.max_margin_utilization
            / self.settings.perp_margin_fraction,
        )


def _hedge_error_bps(spot_notional: float, perp_notional: float) -> float:
    reference = max((abs(spot_notional) + abs(perp_notional)) / 2.0, 1e-12)
    return abs(spot_notional - perp_notional) / reference * 10_000.0


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("strategy timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()
