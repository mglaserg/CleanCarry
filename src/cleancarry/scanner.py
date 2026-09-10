from __future__ import annotations

import math
import statistics
from dataclasses import asdict
from typing import Any, Iterable

from .config import Settings
from .hyperliquid import HyperliquidInfoClient
from .models import CarryMarket, Opportunity

HOURS_PER_YEAR = 365.0 * 24.0


def _f(value: Any, default: float = math.nan) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _isfinite(value: float) -> bool:
    return math.isfinite(value)


def _ewma(values: list[float], span: int) -> float | None:
    values = [x for x in values if _isfinite(x)]
    if not values:
        return None
    alpha = 2.0 / (span + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _funding_vol(values: list[float], lookback: int = 72) -> float | None:
    tail = [x for x in values[-lookback:] if _isfinite(x)]
    if len(tail) < 2:
        return None
    return statistics.stdev(tail)


def _book_spread_bps(book: Any) -> float | None:
    try:
        levels = book["levels"]
        bid = float(levels[0][0]["px"])
        ask = float(levels[1][0]["px"])
        mid = (bid + ask) / 2.0
        if bid <= 0 or ask <= 0 or mid <= 0 or ask < bid:
            return None
        return (ask - bid) / mid * 10_000.0
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def parse_predicted_fundings(payload: Any) -> dict[str, float]:
    """Extract Hyperliquid's own next funding estimate from the venue matrix.

    The API returns coin -> list[venue, details]. Venue labels may change, so the parser
    first prefers a label containing 'Hl'/'Hyperliquid', then falls back to a single venue.
    """
    out: dict[str, float] = {}
    if not isinstance(payload, list):
        return out
    for item in payload:
        if not isinstance(item, list) or len(item) != 2:
            continue
        coin, venues = item
        candidates: list[tuple[str, float]] = []
        if isinstance(venues, list):
            for venue in venues:
                if not isinstance(venue, list) or len(venue) != 2 or not isinstance(venue[1], dict):
                    continue
                rate = _f(venue[1].get("fundingRate"))
                if _isfinite(rate):
                    candidates.append((str(venue[0]), rate))
        preferred = [x for x in candidates if x[0].lower() == "hlperp"]
        if preferred:
            out[str(coin)] = preferred[0][1]
    return out


def build_intersection(
    perp_payload: Any, spot_payload: Any, aliases: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    aliases = aliases or {}
    perp_meta, perp_ctxs = perp_payload
    spot_meta, spot_ctxs = spot_payload

    perp: dict[str, dict[str, Any]] = {}
    for meta, ctx in zip(perp_meta.get("universe", []), perp_ctxs):
        coin = str(meta.get("name", ""))
        if coin:
            perp[coin] = {"meta": meta, "ctx": ctx}

    tokens = {int(t["index"]): t for t in spot_meta.get("tokens", []) if "index" in t}
    rows: list[dict[str, Any]] = []
    for market, ctx in zip(spot_meta.get("universe", []), spot_ctxs):
        token_ids = market.get("tokens", [])
        if len(token_ids) != 2:
            continue
        base = tokens.get(int(token_ids[0]), {})
        quote = tokens.get(int(token_ids[1]), {})
        base_name = str(base.get("name", ""))
        quote_name = str(quote.get("name", ""))
        perp_name = aliases.get(base_name, base_name)
        # V0.1 deliberately restricts clean carry to canonical USDC-quoted spot.
        if not bool(market.get("isCanonical", False)):
            continue
        if quote_name != "USDC" or perp_name not in perp:
            continue
        rows.append(
            {
                "coin": perp_name,
                "spot_token": base_name,
                "spot_market": str(market.get("name", "")),
                "spot_is_canonical": bool(market.get("isCanonical", False)),
                "spot_ctx": ctx,
                "perp_ctx": perp[perp_name]["ctx"],
            }
        )
    return rows


def expected_funding_rate(
    current: float,
    predicted: float | None,
    ewma24: float | None,
    ewma72: float | None,
) -> float:
    weighted = [(current, 0.25), (predicted, 0.35), (ewma24, 0.25), (ewma72, 0.15)]
    valid = [(float(v), w) for v, w in weighted if v is not None and _isfinite(float(v))]
    if not valid:
        return math.nan
    total_w = sum(w for _, w in valid)
    return sum(v * w for v, w in valid) / total_w


def score_market(market: CarryMarket, settings: Settings) -> Opportunity:
    expected = expected_funding_rate(
        market.current_funding_hourly,
        market.predicted_funding_hourly,
        market.ewma_24h,
        market.ewma_72h,
    )
    gross_apr = expected * HOURS_PER_YEAR

    spread_cost = (market.spot_spread_bps or 0.0) + (market.perp_spread_bps or 0.0)
    round_trip_bps = settings.round_trip_fees_bps + spread_cost
    cost_fraction = round_trip_bps / 10_000.0
    cost_apr = cost_fraction * HOURS_PER_YEAR / max(settings.expected_hold_hours, 1)
    net_apr = gross_apr - cost_apr - settings.basis_risk_buffer_apr

    reasons: list[str] = []
    if market.spot_day_volume_usd < settings.min_spot_day_volume_usd:
        reasons.append("spot volume")
    if market.perp_day_volume_usd < settings.min_perp_day_volume_usd:
        reasons.append("perp volume")
    if abs(market.basis_bps) > settings.max_abs_basis_bps:
        reasons.append("basis")
    if market.current_funding_hourly <= 0:
        reasons.append("current funding <= 0")
    if market.predicted_funding_hourly is None:
        reasons.append("predicted funding unavailable")
    elif market.predicted_funding_hourly <= 0:
        reasons.append("predicted funding <= 0")
    if market.ewma_24h is None:
        reasons.append("funding history unavailable")
    elif market.ewma_24h <= 0:
        reasons.append("EWMA24 funding <= 0")
    for label, spread in (("spot spread", market.spot_spread_bps), ("perp spread", market.perp_spread_bps)):
        if spread is None:
            reasons.append(f"{label} unavailable")
        elif spread > settings.max_spread_bps_per_leg:
            reasons.append(label)
    if not _isfinite(net_apr) or net_apr < settings.min_net_apr:
        reasons.append("net APR hurdle")

    return Opportunity(
        coin=market.coin,
        spot_market=market.spot_market,
        expected_funding_hourly=expected,
        gross_funding_apr=gross_apr,
        estimated_round_trip_cost_bps=round_trip_bps,
        cost_apr_at_expected_hold=cost_apr,
        basis_risk_buffer_apr=settings.basis_risk_buffer_apr,
        expected_net_apr=net_apr,
        basis_bps=market.basis_bps,
        current_funding_apr=market.current_funding_hourly * HOURS_PER_YEAR,
        predicted_funding_apr=(
            market.predicted_funding_hourly * HOURS_PER_YEAR
            if market.predicted_funding_hourly is not None
            else None
        ),
        spot_day_volume_usd=market.spot_day_volume_usd,
        perp_day_volume_usd=market.perp_day_volume_usd,
        spot_spread_bps=market.spot_spread_bps,
        perp_spread_bps=market.perp_spread_bps,
        eligible=not reasons,
        reason="eligible" if not reasons else ", ".join(reasons),
    )


def scan(client: HyperliquidInfoClient, settings: Settings) -> tuple[list[CarryMarket], list[Opportunity], dict[str, Any]]:
    perp_payload = client.perp_meta_and_contexts()
    spot_payload = client.spot_meta_and_contexts()
    predicted_raw = client.predicted_fundings()
    predicted = parse_predicted_fundings(predicted_raw)
    intersection = build_intersection(perp_payload, spot_payload, settings.alias_map())

    base_rows: list[dict[str, Any]] = []
    for row in intersection:
        sctx, pctx = row["spot_ctx"], row["perp_ctx"]
        smid = _f(sctx.get("midPx"))
        pmid = _f(pctx.get("midPx"), _f(pctx.get("markPx")))
        if not (_isfinite(smid) and _isfinite(pmid) and smid > 0 and pmid > 0):
            continue
        base_rows.append(
            {
                "coin": row["coin"],
                "spot_market": row["spot_market"],
                "spot_mid": smid,
                "perp_mid": pmid,
                "spot_day_volume_usd": _f(sctx.get("dayNtlVlm"), 0.0),
                "perp_day_volume_usd": _f(pctx.get("dayNtlVlm"), 0.0),
                "current_funding_hourly": _f(pctx.get("funding"), 0.0),
                "predicted_funding_hourly": predicted.get(row["coin"]),
                "basis_bps": (pmid / smid - 1.0) * 10_000.0,
            }
        )

    # Spend history/book requests on economically plausible names instead of the entire venue.
    ranked = sorted(
        base_rows,
        key=lambda r: (r["current_funding_hourly"], r["perp_day_volume_usd"]),
        reverse=True,
    )
    history_names = {r["coin"] for r in ranked[: settings.history_candidates]}
    book_names = {r["coin"] for r in ranked[: settings.book_candidates]}
    now = client.now_ms()
    start = now - settings.history_hours * 60 * 60 * 1000

    histories: dict[str, list[dict[str, Any]]] = {}
    spreads: dict[str, tuple[float | None, float | None]] = {}
    for row in ranked:
        coin = row["coin"]
        if coin in history_names:
            try:
                histories[coin] = client.funding_history(coin, start, now)
            except Exception:
                histories[coin] = []
        if coin in book_names:
            try:
                perp_spread = _book_spread_bps(client.l2_book(coin))
            except Exception:
                perp_spread = None
            try:
                spot_spread = _book_spread_bps(client.l2_book(row["spot_market"]))
            except Exception:
                spot_spread = None
            spreads[coin] = (spot_spread, perp_spread)

    markets: list[CarryMarket] = []
    for row in base_rows:
        hist = histories.get(row["coin"], [])
        rates = [_f(x.get("fundingRate")) for x in hist if isinstance(x, dict)]
        spot_spread, perp_spread = spreads.get(row["coin"], (None, None))
        markets.append(
            CarryMarket(
                **row,
                ewma_24h=_ewma(rates[-24:], 24),
                ewma_72h=_ewma(rates[-72:], 72),
                funding_vol_72h=_funding_vol(rates, 72),
                spot_spread_bps=spot_spread,
                perp_spread_bps=perp_spread,
            )
        )

    opportunities = [score_market(m, settings) for m in markets]
    opportunities.sort(key=lambda x: x.expected_net_apr, reverse=True)
    raw = {
        "perp_payload": perp_payload,
        "spot_payload": spot_payload,
        "predicted_raw": predicted_raw,
        "histories": histories,
    }
    return markets, opportunities, raw


def normalize_perp_contexts(payload: Any) -> list[dict[str, Any]]:
    meta, ctxs = payload
    return [{"coin": m.get("name"), **c} for m, c in zip(meta.get("universe", []), ctxs)]


def normalize_spot_contexts(payload: Any) -> list[dict[str, Any]]:
    meta, ctxs = payload
    markets = meta.get("universe", [])
    return [{"market": m.get("name"), **c} for m, c in zip(markets, ctxs)]


def normalize_predicted(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        if not isinstance(item, list) or len(item) != 2:
            continue
        coin, venues = item
        if not isinstance(venues, list):
            continue
        for venue in venues:
            if isinstance(venue, list) and len(venue) == 2 and isinstance(venue[1], dict):
                rows.append({"coin": coin, "venue": venue[0], **venue[1]})
    return rows
