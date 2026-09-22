from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from .account import summarize_account
from .archive import upsert_timeseries, write_snapshot
from .config import Settings
from .hyperliquid import HyperliquidInfoClient
from .live import (
    ARM_CONFIRMATION,
    HyperliquidLiveExecutor,
    LivePairExecutor,
    LiveStateStore,
    account_equity_usd,
)
from .models import RejectionCode
from .paper import AutonomousPaperStrategy, PaperStateStore
from .research import StudyConfig, run_carry_study
from .scanner import (
    normalize_perp_contexts,
    normalize_predicted,
    normalize_spot_contexts,
    scan,
)

app = typer.Typer(no_args_is_help=True, help="CleanCarry — Hyperliquid delta-neutral carry research stack")
console = Console()


def _settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


@app.command()
def opportunities(
    all_markets: bool = typer.Option(False, "--all", help="Show ineligible names too."),
    limit: int = typer.Option(20, min=1, max=200),
    archive: bool = typer.Option(True, "--archive/--no-archive"),
) -> None:
    """Scan spot/perp intersections and rank expected net clean carry."""
    s = _settings()
    with HyperliquidInfoClient(s.base_url) as client:
        markets, opps, raw = scan(client, s)

    if archive:
        _archive_scan(s, markets, opps, raw)

    shown = opps if all_markets else [o for o in opps if o.eligible]
    table = Table(title="CleanCarry opportunities")
    table.add_column("Coin")
    table.add_column("Net APR", justify="right")
    table.add_column("Gross APR", justify="right")
    table.add_column("Basis", justify="right")
    table.add_column("Spot vol", justify="right")
    table.add_column("Perp vol", justify="right")
    table.add_column("Spreads s/p", justify="right")
    table.add_column("Status")
    for o in shown[:limit]:
        spreads = f"{_bps(o.spot_spread_bps)}/{_bps(o.perp_spread_bps)}"
        table.add_row(
            o.coin,
            _pct(o.expected_net_apr),
            _pct(o.gross_funding_apr),
            f"{o.basis_bps:.1f}bp",
            _money(o.spot_day_volume_usd),
            _money(o.perp_day_volume_usd),
            spreads,
            o.reason,
        )
    console.print(table)
    console.print(
        f"[dim]Perps={len(raw['universe'])} | Intersection={len(markets)} | "
        f"Eligible={sum(o.eligible for o in opps)} | "
        f"entry hurdle={s.min_net_apr:.1%} net APR | expected hold={s.expected_hold_hours}h[/dim]"
    )


@app.command()
def autonomous(
    mode: str | None = typer.Option(
        None, "--mode", help="read_only or shadow. Live remains intentionally unavailable."
    ),
    once: bool = typer.Option(False, "--once", help="Run one cycle instead of the service loop."),
) -> None:
    """Run the autonomous decision pipeline in read-only or persistent shadow mode."""
    s = _settings()
    selected_mode = (mode or s.execution_mode).strip().lower()
    if selected_mode == "live":
        console.print("[bold red]Live execution is not implemented or armed.[/bold red]")
        raise typer.Exit(code=2)
    if selected_mode not in {"read_only", "shadow"}:
        raise typer.BadParameter("--mode must be read_only or shadow")

    strategy = AutonomousPaperStrategy(s, PaperStateStore(s.state_dir))
    while True:
        cycle_started = datetime.now(UTC)
        with HyperliquidInfoClient(s.base_url) as client:
            markets, opps, raw = scan(client, s)
        _archive_scan(s, markets, opps, raw)
        prices = {market.coin: (market.spot_mid, market.perp_mid) for market in markets}
        report = strategy.run_cycle(
            opps,
            prices,
            now=cycle_started,
            mode=selected_mode,
            discovered_count=len(raw["universe"]),
        )
        cycle_row = report.asdict()
        for field in ("selected", "active_coins", "actions", "rejection_counts"):
            cycle_row[field] = json.dumps(cycle_row[field], sort_keys=True)
        write_snapshot([cycle_row], s.data_dir / "derived", "strategy_cycles")
        _render_cycle(report)
        if once:
            return
        elapsed = (datetime.now(UTC) - cycle_started).total_seconds()
        time.sleep(max(s.strategy_cycle_seconds - elapsed, 1))


@app.command("paper-status")
def paper_status() -> None:
    """Show persistent shadow portfolio state and recent execution events."""
    s = _settings()
    store = PaperStateStore(s.state_dir)
    state = store.load()
    console.print(
        f"[bold]Status: {state.status}[/bold] | cycles={state.cycle_count} | "
        f"last={state.last_cycle_at_utc or 'never'}"
    )
    if state.positions:
        table = Table(title="CleanCarry shadow positions")
        for name in (
            "Coin",
            "Spot notional",
            "Perp notional",
            "Hedge error",
            "Net APR",
            "Funding",
            "Opened",
        ):
            table.add_column(name)
        for position in sorted(state.positions.values(), key=lambda item: item.coin):
            table.add_row(
                position.coin,
                _money(
                    position.spot_quantity
                    * (position.last_spot_px or position.entry_spot_px)
                ),
                _money(
                    position.perp_quantity
                    * (position.last_perp_px or position.entry_perp_px)
                ),
                f"{position.hedge_error_bps:.1f}bp",
                _pct(position.current_expected_net_apr),
                _money(position.funding_accrued_usd),
                position.opened_at_utc,
            )
        console.print(table)
    if state.unresolved_exposure:
        console.print(f"[bold red]Unresolved exposure: {state.unresolved_exposure}[/bold red]")


@app.command("paper-control")
def paper_control(
    action: str = typer.Argument(..., help="pause, resume, safe, close, close-all, or kill"),
    coin: str | None = typer.Option(None, "--coin", help="Required for close."),
) -> None:
    """Change persistent shadow controls; this never arms the separate live executor."""
    s = _settings()
    store = PaperStateStore(s.state_dir)
    state = store.load()
    normalized = action.strip().lower()
    if normalized == "pause":
        state.status = "PAUSED"
    elif normalized == "resume":
        if state.unresolved_exposure:
            raise typer.BadParameter("cannot resume with unresolved exposure")
        state.status = "RUNNING"
    elif normalized == "safe":
        state.status = "SAFE_MODE"
    elif normalized == "close":
        if not coin:
            raise typer.BadParameter("--coin is required for close")
        target = coin.strip().upper()
        if target not in state.positions:
            raise typer.BadParameter(f"no managed shadow position for {target}")
        if target not in state.close_requests:
            state.close_requests.append(target)
    elif normalized in {"close-all", "kill"}:
        state.close_requests = sorted(state.positions)
        if normalized == "kill":
            state.status = "SAFE_MODE"
    else:
        raise typer.BadParameter("action must be pause, resume, safe, close, close-all, or kill")
    store.append_event(
        {
            "at_utc": datetime.now(UTC).isoformat(),
            "action": "OPERATOR_CONTROL",
            "control": normalized,
            "coin": coin,
        }
    )
    store.save(state)
    console.print(f"Shadow control accepted: {normalized}; status={state.status}")


@app.command()
def status() -> None:
    """Read Hyperliquid perp and spot account state. No signing/private key required."""
    s = _settings()
    if not s.account_address:
        raise typer.BadParameter("Set HL_ACCOUNT_ADDRESS in the environment or .env loader shell.")
    with HyperliquidInfoClient(s.base_url) as client:
        perp = client.clearinghouse_state(s.account_address)
        spot = client.spot_clearinghouse_state(s.account_address)
    summary, positions, balances = summarize_account(perp, spot)

    table = Table(title="CleanCarry account status")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Account value", _money(summary["account_value"]))
    table.add_row("Margin used", _money(summary["total_margin_used"]))
    table.add_row("Margin utilization", _pct(summary["margin_utilization"]))
    table.add_row("Perp notional", _money(summary["total_perp_notional"]))
    table.add_row("Withdrawable", _money(summary["withdrawable"]))
    console.print(table)

    if positions:
        console.print(pd.DataFrame(positions).to_string(index=False))
    if balances:
        console.print(pd.DataFrame(balances).to_string(index=False))

    write_snapshot([summary], s.data_dir / "raw", "account_summary")
    if positions:
        write_snapshot(positions, s.data_dir / "raw", "perp_positions")
    if balances:
        write_snapshot(balances, s.data_dir / "raw", "spot_balances")


@app.command()
def funding(days: int = typer.Option(7, min=1, max=90)) -> None:
    """Archive realized funding payments for the configured account."""
    s = _settings()
    if not s.account_address:
        raise typer.BadParameter("Set HL_ACCOUNT_ADDRESS first.")
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    with HyperliquidInfoClient(s.base_url) as client:
        rows = client.user_funding(
            s.account_address,
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
        )
    if not rows:
        console.print("No funding rows returned.")
        return
    normalized = []
    for row in rows:
        if isinstance(row, dict):
            delta = row.get("delta", {}) if isinstance(row.get("delta"), dict) else {}
            normalized.append({**row, **{f"delta_{k}": v for k, v in delta.items()}})
    path = upsert_timeseries(
        normalized,
        s.data_dir / "raw" / "user_funding.parquet",
        subset=[c for c in ["time", "hash", "delta_coin"] if c in normalized[0]],
        sort_by=["time"] if "time" in normalized[0] else list(normalized[0])[:1],
    )
    console.print(f"Archived {len(normalized)} funding rows -> {path}")


@app.command()
def replay(
    coin: str = typer.Argument(..., help="Perpetual coin name, for example BTC."),
    as_of_utc: str | None = typer.Option(
        None,
        "--as-of-utc",
        help="Inclusive timezone-aware decision cutoff, for example 2026-09-21T12:00:00Z.",
    ),
    notional_usd: float = typer.Option(1_000.0, min=1.0),
    minimum_trades: int = typer.Option(20, min=1),
) -> None:
    """Replay one archived carry pair and save a reproducible model comparison."""
    s = _settings()
    try:
        cutoff = _parse_utc(as_of_utc) if as_of_utc is not None else None
        result = run_carry_study(
            derived_dir=s.data_dir / "derived",
            output_root=s.data_dir / "derived" / "studies",
            settings=s,
            config=StudyConfig(
                coin=coin,
                as_of_utc=cutoff,
                notional_usd=notional_usd,
                entry_net_apr=s.min_net_apr,
                exit_net_apr=s.exit_net_apr,
                minimum_trades_for_decision=minimum_trades,
            ),
        )
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    table = Table(title=f"CleanCarry replay — {coin.upper()}")
    table.add_column("Model")
    table.add_column("Trades", justify="right")
    table.add_column("Funding", justify="right")
    table.add_column("Spot", justify="right")
    table.add_column("Perp", justify="right")
    table.add_column("Costs", justify="right")
    table.add_column("Net", justify="right")
    for name in ("ensemble", "current_only"):
        summary = result.summaries[name]
        costs = summary.total_fee_cost_usd + summary.total_slippage_cost_usd
        table.add_row(
            name,
            str(summary.trade_count),
            _money(summary.total_funding_pnl_usd),
            _money(summary.total_spot_pnl_usd),
            _money(summary.total_perp_pnl_usd),
            _money(costs),
            _money(summary.total_net_pnl_usd),
        )
    console.print(table)
    console.print(f"[bold]Decision: {result.decision}[/bold] — {result.decision_reason}")
    console.print(f"Manifest: {result.manifest_path}")


@app.command("live-control")
def live_control(
    action: str = typer.Argument(..., help="arm, disarm, safe, or acknowledge"),
    confirm: str | None = typer.Option(None, "--confirm"),
) -> None:
    """Persistently arm or stop direct Hyperliquid execution."""
    s = _settings()
    store = LiveStateStore(s.state_dir)
    state = store.load()
    normalized = action.strip().lower()
    if normalized == "arm":
        if confirm != ARM_CONFIRMATION:
            raise typer.BadParameter(f"arming requires --confirm {ARM_CONFIRMATION}")
        if not s.live_trading_enabled:
            raise typer.BadParameter("set LIVE_TRADING_ENABLED=true before arming")
        if not s.account_address or not s.api_wallet_private_key:
            raise typer.BadParameter(
                "HL_ACCOUNT_ADDRESS and HL_API_WALLET_PRIVATE_KEY are required"
            )
        state.status = "ARMED"
        state.account_address = s.subaccount_address or s.account_address
    elif normalized in {"disarm", "safe"}:
        state.status = "DISARMED" if normalized == "disarm" else "SAFE_MODE"
    elif normalized == "acknowledge":
        if confirm != ARM_CONFIRMATION:
            raise typer.BadParameter(f"acknowledgement requires --confirm {ARM_CONFIRMATION}")
        state.pending_action = None
        state.status = "SAFE_MODE"
    else:
        raise typer.BadParameter("action must be arm, disarm, safe, or acknowledge")
    store.save(state)
    console.print(f"Live control accepted: {normalized}; status={state.status}")


@app.command()
def live(
    once: bool = typer.Option(False, "--once", help="Run one live cycle instead of continuously."),
) -> None:
    """Run direct, persistently armed Hyperliquid spot/perpetual execution."""
    s = _settings()
    store = LiveStateStore(s.state_dir)
    state = store.load()
    if not s.live_trading_enabled or state.status != "ARMED":
        raise typer.BadParameter(
            "live execution is disarmed; set LIVE_TRADING_ENABLED=true and run "
            f"live-control arm --confirm {ARM_CONFIRMATION}"
        )
    if state.pending_action is not None:
        state.status = "SAFE_MODE"
        store.save(state)
        raise typer.BadParameter("unresolved prior live action; entered SAFE_MODE")
    trading_address = s.subaccount_address or s.account_address
    if not trading_address or state.account_address != trading_address:
        raise typer.BadParameter("armed account does not match current account configuration")
    venue = HyperliquidLiveExecutor(s)
    executor = LivePairExecutor(s, store, venue)

    while True:
        started = datetime.now(UTC)
        try:
            _run_live_cycle(s, store, state, executor, trading_address, started)
        except Exception:
            state.status = "SAFE_MODE"
            store.save(state)
            raise
        if once:
            return
        elapsed = (datetime.now(UTC) - started).total_seconds()
        time.sleep(max(s.strategy_cycle_seconds - elapsed, 1))


def _run_live_cycle(
    s: Settings,
    store: LiveStateStore,
    state,
    executor: LivePairExecutor,
    trading_address: str,
    now: datetime,
) -> None:
    with HyperliquidInfoClient(s.base_url) as client:
        markets, opportunities, raw = scan(client, s)
        perp_state = client.clearinghouse_state(trading_address)
        spot_state = client.spot_clearinghouse_state(trading_address)
    _archive_scan(s, markets, opportunities, raw)
    market_by_coin = {market.coin: market for market in markets}
    opportunity_by_coin = {item.coin: item for item in opportunities}
    spot_prices = {(market.spot_token or market.coin): market.spot_mid for market in markets}
    equity = account_equity_usd(perp_state, spot_state, spot_prices)
    if equity <= 0:
        raise RuntimeError("live account equity is zero or unavailable")
    state.last_equity_usd = equity
    state.high_water_equity_usd = max(state.high_water_equity_usd or equity, equity)
    _reconcile_live_positions(state, perp_state, spot_state, s.hedge_tolerance_bps)

    cycle_id = now.strftime("%Y%m%dT%H%M%S%fZ")
    for coin in sorted(state.positions):
        opportunity = opportunity_by_coin.get(coin)
        structural = (
            set(opportunity.rejection_codes) - {RejectionCode.EXPECTED_NET_CARRY_TOO_LOW}
            if opportunity
            else {"MARKET_MISSING"}
        )
        if structural or opportunity.expected_net_apr < s.exit_net_apr:
            executor.exit(state, coin, f"{cycle_id}:EXIT:{coin}")
            console.print(f"[yellow]LIVE EXIT {coin}[/yellow]")
            state.last_cycle_at_utc = now.isoformat()
            store.save(state)
            return

    deployment_limit = min(
        equity * s.live_capital_fraction,
        s.live_max_total_deployment_usd,
        s.max_total_deployment_usd,
    )
    slot_target = deployment_limit / (s.max_positions * (1 + s.perp_margin_fraction))
    for coin in sorted(state.positions):
        market = market_by_coin.get(coin)
        opportunity = opportunity_by_coin.get(coin)
        if market is None or opportunity is None:
            continue
        target = min(slot_target, s.max_per_asset_usd, opportunity.capacity_usd)
        current = state.positions[coin].quantity * market.spot_mid
        half_buffer = target * s.trade_buffer_fraction / 2
        if current < target - half_buffer:
            desired = target - half_buffer - current
            notional = min(desired, s.live_max_order_usd)
            if notional >= s.live_min_order_usd:
                executor.enter(
                    state,
                    coin=coin,
                    spot_market=market.spot_market,
                    spot_token=market.spot_token or market.coin,
                    quantity=notional / market.spot_mid,
                    target_notional_usd=target,
                    action_id=f"{cycle_id}:INCREASE:{coin}",
                )
                console.print(f"[green]LIVE INCREASE {coin} {_money(notional)}[/green]")
                return
        if current > target + half_buffer:
            desired = current - (target + half_buffer)
            notional = min(desired, s.live_max_order_usd)
            if notional >= s.live_min_order_usd:
                executor.reduce(
                    state,
                    coin=coin,
                    quantity=notional / market.spot_mid,
                    target_notional_usd=target,
                    action_id=f"{cycle_id}:REDUCE:{coin}",
                )
                console.print(f"[yellow]LIVE REDUCE {coin} {_money(notional)}[/yellow]")
                return

    ranked = sorted(
        (item for item in opportunities if item.eligible and item.coin not in state.positions),
        key=lambda item: (-item.expected_net_apr, item.coin),
    )
    current_deployment = sum(
        position.quantity * market_by_coin[coin].spot_mid
        for coin, position in state.positions.items()
        if coin in market_by_coin
    )
    for opportunity in ranked:
        if len(state.positions) >= s.max_positions:
            break
        market = market_by_coin[opportunity.coin]
        notional = min(
            slot_target,
            s.live_max_order_usd,
            s.max_per_asset_usd,
            opportunity.capacity_usd,
            max(deployment_limit - current_deployment, 0),
        )
        if notional < s.live_min_order_usd:
            continue
        executor.enter(
            state,
            coin=market.coin,
            spot_market=market.spot_market,
            spot_token=market.spot_token or market.coin,
            quantity=notional / market.spot_mid,
            target_notional_usd=slot_target,
            action_id=f"{cycle_id}:ENTER:{market.coin}",
        )
        console.print(f"[bold green]LIVE ENTER {market.coin} {_money(notional)}[/bold green]")
        break
    state.last_cycle_at_utc = now.isoformat()
    store.save(state)
    console.print(
        f"Live cycle complete | equity={_money(equity)} | target deployment={_money(deployment_limit)}"
    )


def _reconcile_live_positions(state, perp_state, spot_state, tolerance_bps: float) -> None:
    perp_sizes = {
        wrapper.get("position", {}).get("coin"): float(
            wrapper.get("position", {}).get("szi", 0) or 0
        )
        for wrapper in perp_state.get("assetPositions", [])
    }
    spot_sizes = {
        balance.get("coin"): float(balance.get("total", 0) or 0)
        for balance in spot_state.get("balances", [])
    }
    tolerance = tolerance_bps / 10_000
    for position in state.positions.values():
        spot_quantity = spot_sizes.get(position.spot_token, 0.0)
        short_quantity = max(-perp_sizes.get(position.coin, 0.0), 0.0)
        allowed = max(position.quantity * tolerance, 1e-10)
        if (
            abs(spot_quantity - position.quantity) > allowed
            or abs(short_quantity - position.quantity) > allowed
        ):
            state.status = "SAFE_MODE"
            raise RuntimeError(f"live reconciliation mismatch for {position.coin}")


def _pct(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x:.1%}"


def _bps(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x:.1f}"


def _money(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "—"
    if abs(x) >= 1_000_000:
        return f"${x/1_000_000:.1f}m"
    if abs(x) >= 1_000:
        return f"${x/1_000:.1f}k"
    return f"${x:,.2f}"


def _parse_utc(value: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("--as-of-utc must include a timezone, such as Z or +00:00")
    return timestamp.tz_convert("UTC").to_pydatetime()


def _archive_scan(s: Settings, markets: list, opps: list, raw: dict) -> None:
    raw_dir = s.data_dir / "raw"
    derived_dir = s.data_dir / "derived"
    write_snapshot(normalize_perp_contexts(raw["perp_payload"]), raw_dir, "perp_contexts")
    write_snapshot(normalize_spot_contexts(raw["spot_payload"]), raw_dir, "spot_contexts")
    pred_rows = normalize_predicted(raw["predicted_raw"])
    if pred_rows:
        write_snapshot(pred_rows, raw_dir, "predicted_fundings")
    for coin, hist in raw["histories"].items():
        if hist:
            normalized = [{**row, "coin": row.get("coin", coin)} for row in hist]
            upsert_timeseries(
                normalized,
                raw_dir / f"funding_history_{coin}.parquet",
                subset=["coin", "time"],
                sort_by=["coin", "time"],
            )
    for coin, legs in raw.get("volume_candles", {}).items():
        rows = [
            {**candle, "coin": coin, "leg": leg}
            for leg, candles in legs.items()
            for candle in candles
            if isinstance(candle, dict)
        ]
        if rows:
            upsert_timeseries(
                rows,
                raw_dir / f"daily_volume_{coin}.parquet",
                subset=["coin", "leg", "t"],
                sort_by=["coin", "leg", "t"],
            )
    write_snapshot([m.asdict() for m in markets], derived_dir, "carry_markets")
    write_snapshot([o.asdict() for o in opps], derived_dir, "opportunities")
    write_snapshot([item.asdict() for item in raw["universe"]], derived_dir, "universe_funnel")


def _render_cycle(report) -> None:
    console.print(
        f"[bold]{report.status}[/bold] mode={report.mode} | discovered={report.discovered} | "
        f"eligible={report.eligible} | selected={len(report.selected)} | "
        f"active={len(report.active_coins)} | deployed={_money(report.deployed_notional_usd)} | "
        f"free={_money(report.free_strategy_capital_usd)} | "
        f"margin={_pct(report.margin_utilization)}"
    )
    if report.selected:
        table = Table(title="Automatic selections")
        for name in ("Rank", "Coin", "Notional", "Net APR", "Why"):
            table.add_column(name)
        for item in report.selected:
            table.add_row(
                str(item.rank), item.coin, _money(item.notional_usd), _pct(item.expected_net_apr), item.reason
            )
        console.print(table)
    if report.actions:
        console.print(pd.DataFrame([asdict(item) for item in report.actions]).to_string(index=False))


if __name__ == "__main__":
    app()
