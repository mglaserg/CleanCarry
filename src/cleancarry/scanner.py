from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import Settings
from .hyperliquid import HyperliquidInfoClient
from .models import CarryMarket, Opportunity, RejectionCode, UniverseEvaluation

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


def _book_metrics(book: Any, impact_bps: float) -> tuple[float | None, float | None]:
    """Return top-of-book spread and conservative two-sided executable depth in USD."""
    spread = _book_spread_bps(book)
    try:
        levels = book["levels"]
        bid = float(levels[0][0]["px"])
        ask = float(levels[1][0]["px"])
        mid = (bid + ask) / 2.0
        lower = mid * (1.0 - impact_bps / 10_000.0)
        upper = mid * (1.0 + impact_bps / 10_000.0)

        def side_depth(rows: list[dict[str, Any]], *, is_bid: bool) -> float:
            total = 0.0
            for level in rows:
                px = float(level["px"])
                size = float(level["sz"])
                if (is_bid and px < lower) or (not is_bid and px > upper):
                    continue
                total += px * size
            return total

        depth = min(
            side_depth(levels[0], is_bid=True),
            side_depth(levels[1], is_bid=False),
        )
        return spread, depth if math.isfinite(depth) else None
    except (KeyError, IndexError, TypeError, ValueError):
        return spread, None


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
    if (
        market.spot_day_volume_usd < settings.min_spot_day_volume_usd
        or market.perp_day_volume_usd < settings.min_perp_day_volume_usd
    ):
        reasons.append(RejectionCode.INSUFFICIENT_VOLUME)
    if settings.min_perp_open_interest_usd > 0 and (
        market.perp_open_interest_usd is None
        or market.perp_open_interest_usd < settings.min_perp_open_interest_usd
    ):
        reasons.append(RejectionCode.INSUFFICIENT_OPEN_INTEREST)
    if abs(market.basis_bps) > settings.max_abs_basis_bps:
        reasons.append(RejectionCode.BASIS_TOO_WIDE)
    if market.current_funding_hourly <= 0:
        reasons.append(RejectionCode.FUNDING_TOO_LOW)
    if market.predicted_funding_hourly is None or market.predicted_funding_hourly <= 0:
        reasons.append(RejectionCode.FUNDING_TOO_LOW)
    if market.ewma_24h is None or (
        market.funding_history_count > 0
        and market.funding_history_count < settings.min_funding_history
    ):
        reasons.append(RejectionCode.INSUFFICIENT_HISTORY)
    elif market.ewma_24h <= 0:
        reasons.append(RejectionCode.FUNDING_TOO_LOW)
    for spread in (market.spot_spread_bps, market.perp_spread_bps):
        if spread is None or spread > settings.max_spread_bps_per_leg:
            reasons.append(RejectionCode.SPREAD_TOO_WIDE)
    if settings.min_depth_usd_per_leg > 0:
        for depth in (market.spot_depth_usd, market.perp_depth_usd):
            if depth is None or depth < settings.min_depth_usd_per_leg:
                reasons.append(RejectionCode.INSUFFICIENT_DEPTH)
    if settings.allowlist and market.coin.upper() not in settings.allowlist:
        reasons.append(RejectionCode.ALLOWLIST)
    if market.coin.upper() in settings.denylist:
        reasons.append(RejectionCode.DENYLIST)
    if not _isfinite(net_apr) or net_apr < settings.min_net_apr:
        reasons.append(RejectionCode.EXPECTED_NET_CARRY_TOO_LOW)

    reasons = list(dict.fromkeys(reasons))
    capacity = min(
        market.spot_day_volume_usd * settings.liquidity_participation,
        market.perp_day_volume_usd * settings.liquidity_participation,
        market.spot_depth_usd or 0.0,
        market.perp_depth_usd or 0.0,
    )

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
        reason=RejectionCode.ELIGIBLE if not reasons else ",".join(reasons),
        rejection_codes=tuple(reasons),
        capacity_usd=capacity,
        observed_at_utc=market.observed_at_utc,
    )


def scan(
    client: HyperliquidInfoClient, settings: Settings
) -> tuple[list[CarryMarket], list[Opportunity], dict[str, Any]]:
    observed_at = datetime.now(UTC).isoformat()
    perp_payload = client.perp_meta_and_contexts()
    spot_payload = client.spot_meta_and_contexts()
    predicted_raw = client.predicted_fundings()
    predicted = parse_predicted_fundings(predicted_raw)
    intersection = build_intersection(perp_payload, spot_payload, settings.alias_map())
    matched = {row["coin"]: row for row in intersection}
    perp_meta, _ = perp_payload
    discovered_perps = [
        str(item.get("name", ""))
        for item in perp_meta.get("universe", [])
        if str(item.get("name", ""))
    ]
    universe: list[UniverseEvaluation] = [
        UniverseEvaluation(
            coin=coin,
            spot_market=(str(matched[coin]["spot_market"]) if coin in matched else None),
            stage="SPOT_HEDGEABLE" if coin in matched else "DISCOVERED",
            eligible=False,
            rejection_codes=() if coin in matched else (RejectionCode.NO_SPOT_HEDGE,),
            detail="canonical USDC spot hedge found" if coin in matched else "no canonical USDC spot mapping",
            observed_at_utc=observed_at,
        )
        for coin in discovered_perps
    ]

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
                "perp_open_interest_usd": (
                    _f(pctx.get("openInterest"), math.nan) * pmid
                    if _isfinite(_f(pctx.get("openInterest"), math.nan))
                    else None
                ),
                "observed_at_utc": observed_at,
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
    books: dict[str, tuple[float | None, float | None, float | None, float | None]] = {}
    for row in ranked:
        coin = row["coin"]
        if coin in history_names:
            try:
                histories[coin] = client.funding_history(coin, start, now)
            except (httpx.HTTPError, ValueError):
                histories[coin] = []
        if coin in book_names:
            try:
                perp_spread, perp_depth = _book_metrics(
                    client.l2_book(coin), settings.depth_impact_bps
                )
            except (httpx.HTTPError, ValueError):
                perp_spread, perp_depth = None, None
            try:
                spot_spread, spot_depth = _book_metrics(
                    client.l2_book(row["spot_market"]), settings.depth_impact_bps
                )
            except (httpx.HTTPError, ValueError):
                spot_spread, spot_depth = None, None
            books[coin] = (spot_spread, perp_spread, spot_depth, perp_depth)

    markets: list[CarryMarket] = []
    for row in base_rows:
        hist = histories.get(row["coin"], [])
        rates = [_f(x.get("fundingRate")) for x in hist if isinstance(x, dict)]
        spot_spread, perp_spread, spot_depth, perp_depth = books.get(
            row["coin"], (None, None, None, None)
        )
        markets.append(
            CarryMarket(
                **row,
                ewma_24h=_ewma(rates[-24:], 24),
                ewma_72h=_ewma(rates[-72:], 72),
                funding_vol_72h=_funding_vol(rates, 72),
                spot_spread_bps=spot_spread,
                perp_spread_bps=perp_spread,
                spot_depth_usd=spot_depth,
                perp_depth_usd=perp_depth,
                funding_history_count=len([rate for rate in rates if _isfinite(rate)]),
            )
        )

    opportunities = [score_market(m, settings) for m in markets]
    opportunities.sort(key=lambda x: (x.eligible, x.expected_net_apr), reverse=True)
    opportunity_by_coin = {item.coin: item for item in opportunities}
    universe = [
        UniverseEvaluation(
            coin=item.coin,
            spot_market=item.spot_market,
            stage=("CARRY_QUALIFIED" if opportunity_by_coin[item.coin].eligible else "FILTERED")
            if item.coin in opportunity_by_coin
            else item.stage,
            eligible=(opportunity_by_coin[item.coin].eligible if item.coin in opportunity_by_coin else False),
            rejection_codes=(
                opportunity_by_coin[item.coin].rejection_codes
                if item.coin in opportunity_by_coin
                else (
                    item.rejection_codes
                    if item.rejection_codes
                    else (RejectionCode.INVALID_MARKET_DATA,)
                )
            ),
            detail=(
                opportunity_by_coin[item.coin].reason
                if item.coin in opportunity_by_coin
                else (
                    item.detail
                    if item.rejection_codes
                    else RejectionCode.INVALID_MARKET_DATA
                )
            ),
            observed_at_utc=observed_at,
        )
        for item in universe
    ]
    raw = {
        "perp_payload": perp_payload,
        "spot_payload": spot_payload,
        "predicted_raw": predicted_raw,
        "histories": histories,
        "universe": universe,
        "observed_at_utc": observed_at,
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
