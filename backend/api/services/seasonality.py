"""
Seasonal demand modelling for pre-emptive planning.

Combines three signals into a per-(category, month) demand multiplier that scales
the demand model's baseline (P95-footfall-derived) requirement so the Planning
tab flags shortages BEFORE the season that causes them:

  1. Disease-season calendar  — clinical seasonality of Indian public health
     (monsoon → waterborne/vector: ORS, antimalarial, fever/antibiotics;
      winter → respiratory: antibiotics, analgesics; summer → heat/dehydration).
     Static, self-contained, no external dependency.
  2. Historical footfall lift — the district's own month-over-month footfall
     index from daily_snapshots (target month avg ÷ annual avg). Computed by the
     caller and passed in.
  3. Live weather anomaly     — best-effort near-term forecast (rain/heat) via a
     weather API, applied to rain/heat-sensitive categories. Gracefully skipped
     when OPENWEATHER_API_KEY is not configured or the call fails.
"""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)

# Indian season month buckets (1 = Jan).
_MONSOON = {6, 7, 8, 9}
_WINTER = {11, 12, 1, 2}
_SUMMER = {3, 4, 5}

# Categories whose demand rises with heavy rainfall (waterborne/vector-borne).
_WEATHER_CACHE: dict = {}  # {(lat1, lng1): {"rain": x, "heat": y}} process-lifetime
RAIN_SENSITIVE = {"ORS", "ANTIMALARIAL", "ANTIBIOTIC"}
# Categories whose demand rises in extreme heat (dehydration).
HEAT_SENSITIVE = {"ORS"}


def disease_season_multiplier(category: str, month: int) -> float:
    """Clinical seasonality multiplier for a medicine category in a given month."""
    c = (category or "").upper()
    if c == "ORS":
        if month in _MONSOON:
            return 1.6
        if month in _SUMMER:
            return 1.4
        return 1.0
    if c == "ANTIMALARIAL":
        return 1.8 if month in _MONSOON else 0.9
    if c == "ANTIBIOTIC":
        if month in _WINTER:
            return 1.4
        if month in _MONSOON:
            return 1.3
        return 1.0
    if c == "ANALGESIC":  # fever/body-ache load in monsoon + winter
        return 1.3 if (month in _MONSOON or month in _WINTER) else 1.0
    if c == "VACCINE":
        return 1.2 if month in _WINTER else 1.0  # flu-season ramp
    # Chronic-care categories (antihypertensive, antidiabetic) are non-seasonal.
    return 1.0


def combined_multiplier(
    category: str,
    month: int,
    historical_index: float = 1.0,
    weather_factor: float = 1.0,
) -> float:
    """Blend the three signals, clamped to a sane band so no single input can
    produce an absurd order quantity."""
    mult = (
        disease_season_multiplier(category, month)
        * max(0.5, min(historical_index, 2.0))
        * max(1.0, min(weather_factor, 1.5))
    )
    return round(max(0.6, min(mult, 3.0)), 3)


def fetch_weather_factor(lat: float | None, lng: float | None) -> dict[str, float]:
    """Best-effort near-term weather → per-sensitivity multipliers.

    Returns {"rain": x, "heat": y} (1.0 = neutral). Requires OPENWEATHER_API_KEY;
    returns neutral factors when the key is absent, coords are missing, or the
    call fails — planning then relies on the season calendar + history alone."""
    neutral = {"rain": 1.0, "heat": 1.0}
    key = os.environ.get("OPENWEATHER_API_KEY")
    if not key or lat is None or lng is None:
        return neutral
    # Process-lifetime cache keyed by ~11km grid cell — avoids a live HTTP call on
    # every planning request (the dominant latency), since weather barely varies
    # within a district over a planning run.
    ck = (round(lat, 1), round(lng, 1))
    cached = _WEATHER_CACHE.get(ck)
    if cached is not None:
        return cached
    try:
        import httpx

        r = httpx.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"lat": lat, "lon": lng, "appid": key, "units": "metric", "cnt": 16},
            timeout=4.0,
        )
        r.raise_for_status()
        entries = r.json().get("list", [])
        if not entries:
            return neutral
        rain_mm = sum(e.get("rain", {}).get("3h", 0) or 0 for e in entries)
        max_temp = max((e.get("main", {}).get("temp_max", 0) or 0) for e in entries)
        # Heavy cumulative rain over the window lifts waterborne demand; a heat
        # spike lifts dehydration demand. Bounded, gentle nudges.
        rain = 1.0 + min(rain_mm / 100.0, 0.4)   # +40% cap at ≥100mm
        heat = 1.0 + (0.25 if max_temp >= 40 else 0.0)
        result = {"rain": round(rain, 3), "heat": round(heat, 3)}
        _WEATHER_CACHE[ck] = result
        return result
    except Exception as exc:  # noqa: BLE001 - never fail planning on weather
        log.info("weather_fetch_failed", error=str(exc))
        _WEATHER_CACHE[ck] = neutral  # don't retry a failing call every request
        return neutral


def category_weather_factor(category: str, weather: dict[str, float]) -> float:
    """Pick the relevant weather multiplier for a category."""
    c = (category or "").upper()
    factor = 1.0
    if c in RAIN_SENSITIVE:
        factor = max(factor, weather.get("rain", 1.0))
    if c in HEAT_SENSITIVE:
        factor = max(factor, weather.get("heat", 1.0))
    return factor
