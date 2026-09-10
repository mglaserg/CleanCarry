from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from .account import summarize_account
from .archive import upsert_timeseries, write_snapshot
from .config import Settings
from .hyperliquid import HyperliquidInfoClient
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
        write_snapshot([m.asdict() for m in markets], derived_dir, "carry_markets")
        write_snapshot([o.asdict() for o in opps], derived_dir, "opportunities")

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
        f"[dim]Intersection={len(markets)} | Eligible={sum(o.eligible for o in opps)} | "
        f"entry hurdle={s.min_net_apr:.1%} net APR | expected hold={s.expected_hold_hours}h[/dim]"
    )


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
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    with HyperliquidInfoClient(s.base_url) as client:
        rows = client.user_funding(s.account_address, int(start.timestamp() * 1000), int(end.timestamp() * 1000))
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
def live() -> None:
    """Safety latch: M1 intentionally cannot send orders."""
    s = _settings()
    if not s.live_trading_enabled:
        console.print("[yellow]LIVE_TRADING_ENABLED=false. Live trading is disarmed.[/yellow]")
    console.print("[bold]M1 contains no order-sending code by design.[/bold] Complete paper/reconciliation milestone first.")
    raise typer.Exit(code=2)


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


if __name__ == "__main__":
    app()
