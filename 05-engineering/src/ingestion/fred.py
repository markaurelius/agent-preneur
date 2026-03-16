"""Build a historical finance events corpus from FRED (Federal Reserve Economic Data).

Events generated:
  - Fed rate change events (each FOMC decision that moved rates ≥25bps)
  - Inflation threshold crossings (CPI crossing 2/4/6/8% up and down)
  - NBER recession start/end events
  - Yield curve inversion/normalisation events
  - S&P 500 correction events (drawdown ≥10%)

Each event gets a narrative description with economic context at the time,
and an outcome describing the 12-month trajectory that followed.

Requires: FRED_API_KEY in environment (free at https://fred.stlouisfed.org/docs/api/api_key.html)
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, date, timedelta
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# FRED series IDs
_SERIES = {
    "fedfunds":   "FEDFUNDS",    # effective federal funds rate (monthly)
    "cpi":        "CPIAUCSL",    # CPI all urban consumers SA (monthly, index)
    "cpi_yoy":    "CPIAUCNS",    # CPI not-SA for YoY calc
    "unrate":     "UNRATE",      # unemployment rate (monthly)
    "t10y2y":     "T10Y2Y",      # 10yr-2yr yield spread (daily)
    "sp500":      "SP500",       # S&P 500 close (daily)
    "usrec":      "USREC",       # NBER recession indicator 0/1 (monthly)
    "gdp":        "A191RL1Q225SBEA",  # real GDP growth QoQ annualised
}


# ---------------------------------------------------------------------------
# FRED API helpers
# ---------------------------------------------------------------------------


def _fetch_series(series_id: str, api_key: str, frequency: str | None = None) -> list[dict]:
    """Fetch all observations for a FRED series. Returns list of {date, value} dicts."""
    params: dict = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": "1950-01-01",
    }
    if frequency:
        params["frequency"] = frequency

    try:
        resp = httpx.get(FRED_BASE, params=params, timeout=30)
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
    except Exception as exc:
        logger.error("Failed to fetch FRED series %s: %s", series_id, exc)
        return []

    result = []
    for obs in observations:
        try:
            val = float(obs["value"])
            result.append({"date": obs["date"], "value": val})
        except (ValueError, KeyError):
            continue  # "." missing values
    return result


def _to_monthly_dict(series: list[dict]) -> dict[str, float]:
    """Return {YYYY-MM: value} from a list of {date, value} dicts."""
    return {obs["date"][:7]: obs["value"] for obs in series}


def _nearest(mapping: dict[str, float], ym: str) -> float | None:
    """Return the value for YYYY-MM, searching up to 3 months back if missing."""
    for delta in range(4):
        y, m = int(ym[:4]), int(ym[5:7])
        m -= delta
        while m <= 0:
            m += 12
            y -= 1
        key = f"{y:04d}-{m:02d}"
        if key in mapping:
            return mapping[key]
    return None


def _cpi_yoy(cpi: dict[str, float], ym: str) -> float | None:
    """Compute year-over-year CPI change (%) for YYYY-MM."""
    y, m = int(ym[:4]), int(ym[5:7])
    prior_year = f"{y - 1:04d}-{m:02d}"
    current = _nearest(cpi, ym)
    prior = _nearest(cpi, prior_year)
    if current is None or prior is None or prior == 0:
        return None
    return round((current - prior) / prior * 100, 2)


def _stable_id(prefix: str, date_str: str, suffix: str = "") -> str:
    key = f"{prefix}-{date_str}-{suffix}"
    return "fred-" + hashlib.sha1(key.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------


def _build_fed_events(
    fedfunds: dict[str, float],
    cpi: dict[str, float],
    unrate: dict[str, float],
    gdp: dict[str, float],
) -> list[dict]:
    """One event per FOMC meeting that resulted in a ≥25bps rate change."""
    months = sorted(fedfunds.keys())
    events = []

    for i in range(1, len(months)):
        prev_ym = months[i - 1]
        curr_ym = months[i]
        prev_rate = fedfunds[prev_ym]
        curr_rate = fedfunds[curr_ym]
        change_bps = round((curr_rate - prev_rate) * 100)

        if abs(change_bps) < 20:
            continue  # not a meaningful move

        direction = "raised" if change_bps > 0 else "cut"
        action_word = "hiking" if change_bps > 0 else "easing"

        cpi_now = _cpi_yoy(cpi, curr_ym)
        un_now = _nearest(unrate, curr_ym)
        gdp_now = _nearest(gdp, curr_ym)

        # Outcome: rate 12 months later
        y, m = int(curr_ym[:4]), int(curr_ym[5:7])
        m += 12
        if m > 12:
            m -= 12
            y += 1
        outcome_ym = f"{y:04d}-{m:02d}"
        outcome_rate = _nearest(fedfunds, outcome_ym)

        context_parts = []
        if cpi_now is not None:
            context_parts.append(f"CPI at {cpi_now:.1f}% YoY")
        if un_now is not None:
            context_parts.append(f"unemployment at {un_now:.1f}%")
        if gdp_now is not None:
            context_parts.append(f"GDP growth {gdp_now:+.1f}% annualised")
        context = ", ".join(context_parts) if context_parts else "macro data unavailable"

        if outcome_rate is not None:
            outcome_delta = round((outcome_rate - curr_rate) * 100)
            if outcome_delta > 0:
                outcome_str = f"Rates rose further to {outcome_rate:.2f}% over the following 12 months."
            elif outcome_delta < 0:
                outcome_str = f"Rates fell to {outcome_rate:.2f}% over the following 12 months."
            else:
                outcome_str = f"Rates held near {outcome_rate:.2f}% over the following 12 months."
        else:
            outcome_str = "Subsequent rate path unavailable."

        # Classify regime
        if abs(change_bps) >= 75:
            regime = "emergency" if change_bps < 0 else "aggressive"
        elif abs(change_bps) >= 50:
            regime = "decisive"
        else:
            regime = "measured"

        year = curr_ym[:4]
        description = (
            f"Federal Reserve {direction} the federal funds rate by {abs(change_bps)}bps "
            f"from {prev_rate:.2f}% to {curr_rate:.2f}% in {curr_ym} ({regime} {action_word} move). "
            f"Economic context: {context}. "
            f"Outcome: {outcome_str}"
        )

        events.append({
            "id": _stable_id("fed", curr_ym, str(change_bps)),
            "description": description,
            "actors": ["Federal Reserve", "US"],
            "event_type": "economic",
            "outcome": outcome_str,
            "date": curr_ym + "-01",
            "region": "North America",
        })

    logger.info("Built %d Fed rate change events", len(events))
    return events


def _build_inflation_events(
    cpi: dict[str, float],
    fedfunds: dict[str, float],
    unrate: dict[str, float],
) -> list[dict]:
    """Events when CPI YoY crosses 2%, 4%, 6%, or 8% thresholds."""
    thresholds = [2.0, 4.0, 6.0, 8.0]
    months = sorted(cpi.keys())
    events = []
    last_state: dict[float, str] = {}  # threshold → "above" | "below"

    for ym in months:
        yoy = _cpi_yoy(cpi, ym)
        if yoy is None:
            continue

        for threshold in thresholds:
            current_state = "above" if yoy >= threshold else "below"
            prev_state = last_state.get(threshold)

            if prev_state is not None and current_state != prev_state:
                direction = "surpassed" if current_state == "above" else "fell below"
                fed_rate = _nearest(fedfunds, ym)
                un = _nearest(unrate, ym)

                # Outcome: CPI 12 months later
                y, m = int(ym[:4]), int(ym[5:7])
                m += 12
                if m > 12:
                    m -= 12
                    y += 1
                out_ym = f"{y:04d}-{m:02d}"
                out_yoy = _cpi_yoy(cpi, out_ym)

                context_parts = []
                if fed_rate is not None:
                    context_parts.append(f"Fed funds at {fed_rate:.2f}%")
                if un is not None:
                    context_parts.append(f"unemployment {un:.1f}%")
                context = ", ".join(context_parts) if context_parts else ""

                if out_yoy is not None:
                    outcome_str = f"CPI reached {out_yoy:.1f}% YoY 12 months later."
                else:
                    outcome_str = "Subsequent inflation data unavailable."

                description = (
                    f"US CPI inflation {direction} {threshold:.0f}% YoY in {ym}, "
                    f"reaching {yoy:.1f}%. "
                    f"{('Context: ' + context + '. ') if context else ''}"
                    f"Outcome: {outcome_str}"
                )

                events.append({
                    "id": _stable_id("cpi", ym, f"{threshold:.0f}{current_state}"),
                    "description": description,
                    "actors": ["US", "Federal Reserve"],
                    "event_type": "economic",
                    "outcome": outcome_str,
                    "date": ym + "-01",
                    "region": "North America",
                })

            last_state[threshold] = current_state

    logger.info("Built %d inflation threshold events", len(events))
    return events


def _build_recession_events(
    usrec: dict[str, float],
    fedfunds: dict[str, float],
    unrate: dict[str, float],
    cpi: dict[str, float],
) -> list[dict]:
    """One event per NBER recession start and end."""
    months = sorted(usrec.keys())
    events = []
    in_recession = False

    recession_start: str | None = None

    for ym in months:
        is_rec = usrec.get(ym, 0) == 1.0

        if is_rec and not in_recession:
            # Recession start
            in_recession = True
            recession_start = ym
            fed = _nearest(fedfunds, ym)
            un = _nearest(unrate, ym)
            inf = _cpi_yoy(cpi, ym)

            context_parts = []
            if fed is not None:
                context_parts.append(f"Fed funds at {fed:.2f}%")
            if un is not None:
                context_parts.append(f"unemployment {un:.1f}%")
            if inf is not None:
                context_parts.append(f"CPI {inf:.1f}%")

            description = (
                f"NBER recession began in {ym}. "
                f"Conditions at onset: {', '.join(context_parts) if context_parts else 'data unavailable'}."
            )
            events.append({
                "id": _stable_id("recession", ym, "start"),
                "description": description,
                "actors": ["US"],
                "event_type": "economic",
                "outcome": "Recession in progress",
                "date": ym + "-01",
                "region": "North America",
            })

        elif not is_rec and in_recession and recession_start:
            # Recession end
            in_recession = False
            start_un = _nearest(unrate, recession_start)
            end_un = _nearest(unrate, ym)

            duration_months = 0
            y1, m1 = int(recession_start[:4]), int(recession_start[5:7])
            y2, m2 = int(ym[:4]), int(ym[5:7])
            duration_months = (y2 - y1) * 12 + (m2 - m1)

            outcome_parts = [f"lasted {duration_months} months"]
            if start_un and end_un:
                outcome_parts.append(
                    f"unemployment rose from {start_un:.1f}% to {end_un:.1f}%"
                )

            outcome_str = "Recession ended. " + "; ".join(outcome_parts) + "."
            # Patch the start event outcome
            events[-1]["outcome"] = outcome_str

            end_un_val = _nearest(unrate, ym)
            description = (
                f"NBER recession ended in {ym} after {duration_months} months "
                f"(started {recession_start}). "
                f"{'Unemployment at peak: ' + str(end_un_val) + '%.' if end_un_val else ''} "
                f"Outcome: {outcome_str}"
            )
            events.append({
                "id": _stable_id("recession", ym, "end"),
                "description": description,
                "actors": ["US"],
                "event_type": "economic",
                "outcome": outcome_str,
                "date": ym + "-01",
                "region": "North America",
            })
            recession_start = None

    logger.info("Built %d recession events", len(events))
    return events


def _build_yield_curve_events(
    t10y2y: dict[str, float],
    fedfunds: dict[str, float],
) -> list[dict]:
    """Events when the 10yr-2yr yield curve inverts or normalises."""
    months = sorted(t10y2y.keys())
    events = []
    inverted = False

    inversion_start: str | None = None

    for ym in months:
        spread = t10y2y.get(ym)
        if spread is None:
            continue

        if spread < 0 and not inverted:
            inverted = True
            inversion_start = ym
            fed = _nearest(fedfunds, ym)
            description = (
                f"US yield curve (10yr-2yr) inverted in {ym}, spread at {spread:.2f}%. "
                f"Fed funds rate at {fed:.2f}% at time of inversion. "
                f"Yield curve inversions have historically preceded recessions by 6–18 months."
            )
            events.append({
                "id": _stable_id("yieldcurve", ym, "inversion"),
                "description": description,
                "actors": ["US", "Federal Reserve"],
                "event_type": "economic",
                "outcome": "Inversion in progress",
                "date": ym + "-01",
                "region": "North America",
            })

        elif spread >= 0 and inverted and inversion_start:
            inverted = False
            y1, m1 = int(inversion_start[:4]), int(inversion_start[5:7])
            y2, m2 = int(ym[:4]), int(ym[5:7])
            duration = (y2 - y1) * 12 + (m2 - m1)
            outcome_str = f"Yield curve normalised after {duration} months of inversion."
            if events:
                events[-1]["outcome"] = outcome_str
            inversion_start = None

    logger.info("Built %d yield curve events", len(events))
    return events


def _build_sp500_events(sp500_raw: list[dict]) -> list[dict]:
    """Events for S&P 500 corrections (≥10% drawdown from recent peak) and recoveries."""
    if not sp500_raw:
        return []

    # Convert to monthly (last trading day of month)
    monthly: dict[str, float] = {}
    for obs in sp500_raw:
        ym = obs["date"][:7]
        monthly[ym] = obs["value"]  # last value wins = end-of-month

    months = sorted(monthly.keys())
    events = []
    peak = monthly[months[0]]
    peak_ym = months[0]
    in_correction = False
    correction_start_ym = ""
    correction_low = peak

    for ym in months:
        val = monthly[ym]
        drawdown = (val - peak) / peak * 100

        if val > peak:
            if in_correction and drawdown > -5:
                # Recovery
                duration_m = 0
                y1, m1 = int(correction_start_ym[:4]), int(correction_start_ym[5:7])
                y2, m2 = int(ym[:4]), int(ym[5:7])
                duration_m = (y2 - y1) * 12 + (m2 - m1)
                max_dd = round((correction_low - peak) / peak * 100, 1)
                outcome_str = f"Market recovered to new highs in {ym} after {duration_m}-month correction (max drawdown {max_dd}%)."
                if events:
                    events[-1]["outcome"] = outcome_str
                in_correction = False

            peak = val
            peak_ym = ym
            correction_low = val

        elif drawdown <= -10 and not in_correction:
            in_correction = True
            correction_start_ym = ym
            correction_low = val

            description = (
                f"S&P 500 entered correction territory in {ym}, "
                f"down {abs(drawdown):.1f}% from its {peak_ym} peak of {peak:.0f}. "
                f"Current level: {val:.0f}."
            )
            events.append({
                "id": _stable_id("sp500", ym, "correction"),
                "description": description,
                "actors": ["S&P 500", "US equity market"],
                "event_type": "economic",
                "outcome": "Correction ongoing",
                "date": ym + "-01",
                "region": "North America",
            })

        if in_correction and val < correction_low:
            correction_low = val

    logger.info("Built %d S&P 500 correction events", len(events))
    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_fred_corpus(api_key: str) -> list[dict]:
    """Fetch FRED data and return a list of finance event dicts.

    Suitable for passing directly to embed_and_store_events().
    """
    logger.info("Fetching FRED series ...")

    fedfunds_raw = _fetch_series(_SERIES["fedfunds"], api_key)
    cpi_raw = _fetch_series(_SERIES["cpi"], api_key)
    unrate_raw = _fetch_series(_SERIES["unrate"], api_key)
    gdp_raw = _fetch_series(_SERIES["gdp"], api_key)
    t10y2y_raw = _fetch_series(_SERIES["t10y2y"], api_key, frequency="m")
    sp500_raw = _fetch_series(_SERIES["sp500"], api_key, frequency="m")
    usrec_raw = _fetch_series(_SERIES["usrec"], api_key)

    fedfunds = _to_monthly_dict(fedfunds_raw)
    cpi = _to_monthly_dict(cpi_raw)
    unrate = _to_monthly_dict(unrate_raw)
    gdp = _to_monthly_dict(gdp_raw)
    t10y2y = _to_monthly_dict(t10y2y_raw)
    usrec = _to_monthly_dict(usrec_raw)

    logger.info(
        "Loaded: fedfunds=%d, cpi=%d, unrate=%d, gdp=%d, t10y2y=%d, sp500=%d, usrec=%d months",
        len(fedfunds), len(cpi), len(unrate), len(gdp), len(t10y2y), len(sp500_raw), len(usrec),
    )

    events: list[dict] = []
    events.extend(_build_fed_events(fedfunds, cpi, unrate, gdp))
    events.extend(_build_inflation_events(cpi, fedfunds, unrate))
    events.extend(_build_recession_events(usrec, fedfunds, unrate, cpi))
    events.extend(_build_yield_curve_events(t10y2y, fedfunds))
    events.extend(_build_sp500_events(sp500_raw))

    # Deduplicate by id
    seen: set[str] = set()
    deduped = []
    for e in events:
        if e["id"] not in seen:
            seen.add(e["id"])
            deduped.append(e)

    logger.info("Total FRED events: %d", len(deduped))
    return deduped
