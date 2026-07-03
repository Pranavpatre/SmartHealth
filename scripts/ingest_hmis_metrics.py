#!/usr/bin/env python3
"""
SmartHealth — HMIS District Metrics Ingestion (real, data.gov.in)

Generalises the OPD-footfall ingestion (ingest_hmis_footfall.py) to ANY HMIS
indicator. The "Item-wise HMIS report of <State>" resources are long-format:
each row is one (district, Parameters, Type) with monthly columns + a Total.
We already mine ONE parameter (OPD attendance); this script mines more —
currently IPD head count and medicine stock-out rate — into district_hmis_metrics.

Granularity: district, annual (latest HMIS year available per state, FY2011-12
to FY2018-19 depending on state). Idempotent + resumable (skips already-ingested
state×period×metric).

Usage:
    DATA_GOV_API_KEY=<key> python scripts/ingest_hmis_metrics.py
    python scripts/ingest_hmis_metrics.py --only "Maharashtra" --metrics ipd_headcount,stockout_rate
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse

import psycopg2
from psycopg2.extras import execute_values

# Reuse the state→resource map and schema-detection knobs from the footfall script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_hmis_footfall import STATE_RESOURCES, ANNUAL_FIELD_CANDIDATES, _to_int  # noqa: E402

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://smarthealth:smarthealth@localhost:5432/smarthealth",
).replace("postgresql+asyncpg://", "postgresql://")
API_KEY = os.environ.get(
    "DATA_GOV_API_KEY", "579b464db66ec23bdd0000018a8cd8224ea9422c499b573016bbceb4"
)
BASE_URL = "https://api.data.gov.in/resource"

MONTHS = [
    "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "january", "february", "march",
]

# metric key → HMIS Parameters candidates (schema/wording varies across states).
METRICS: dict[str, list[str]] = {
    "ipd_headcount": [
        "In-Patient Head Count at midnight",
        "In-Patient Head Count at Midnight",
        "IPD attendance (All)",
    ],
    "stockout_rate": [
        "Health Facility Services/Patient Services/Stock out rate of essential Drugs",
        "Stock out rate of essential Drugs",
    ],
    # Immunisation: prefer "fully immunised", fall back to Measles-1 / Penta-3 as
    # coverage proxies for states whose schema lacks the composite indicator.
    "fully_immunized": [
        "Total number of children (12 to 23 months old) fully immunised (BCG+DPT123+OPV123/Pentavalent123+Measles) during the month (sum of items 10.3.1.a and 10.3.1.b)",
        "Total number of children (12 to 23 months old) fully immunised (BCG+DPT123+OPV123/Pentavalent123+Measles)",
        "Number of Infants (0 to 11 months old) received Measles immunisation (First Dose)",
        "Number of Infants (0 to 11 months old) received Pentavalent3 immunisation",
    ],
    # Maternal: institutional deliveries at PUBLIC institutions (= our PHC/CHC scope).
    "institutional_deliveries": [
        "Deliveries conducted at Public Institutions (Including C-Sections)",
        "Deliveries Conducted at Public Institutions",
        "Number of Institutional deliveries conducted at Public Institutions",
    ],
}


def _curl_json(url: str) -> dict:
    """GET JSON via curl (more reliable than httpx vs data.gov.in's flaky API)."""
    proc = subprocess.run(
        ["curl", "-s", "-g", "--retry", "6", "--retry-all-errors",
         "--retry-delay", "3", "--max-time", "40", url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"curl failed (rc={proc.returncode})")
    return json.loads(proc.stdout)


def _detect(resource_id: str, param_candidates: list[str]) -> tuple[str | None, str | None]:
    """Return (matched_param, annual_field) — probe each parameter candidate for
    rows and pick the annual-total field this resource's schema uses."""
    meta = _curl_json(f"{BASE_URL}/{resource_id}?api-key={API_KEY}&format=json&limit=1")
    field_ids = {f["id"] for f in meta.get("field", [])}
    annual = next((f for f in ANNUAL_FIELD_CANDIDATES if f in field_ids), None)

    param = None
    for cand in param_candidates:
        enc = urllib.parse.quote(cand)
        probe = _curl_json(
            f"{BASE_URL}/{resource_id}?api-key={API_KEY}&format=json&limit=1"
            f"&filters%5Bparameters%5D={enc}"
        )
        if int(probe.get("total", 0)) > 0:
            param = cand
            break
    return param, annual


def _monthly_avg(rec: dict, annual: int | None) -> float | None:
    """Mean of available monthly columns; falls back to annual/12 when the
    resource uses a non-simple month-column schema."""
    vals = [_to_int(rec.get(m)) for m in MONTHS]
    present = [v for v in vals if v is not None]
    if present:
        return round(sum(present) / len(present), 2)
    return round(annual / 12, 2) if annual is not None else None


def fetch_state_metric(resource_id: str, param_candidates: list[str]) -> list[tuple[str, int, float | None]]:
    """Return [(district, annual_value, monthly_avg)] for one state×metric.
    Dedupes by district (keeps the max annual value)."""
    param, annual_field = _detect(resource_id, param_candidates)
    if not param or not annual_field:
        return []

    by_district: dict[str, tuple[int, float | None]] = {}
    offset = 0
    enc = urllib.parse.quote(param)
    while True:
        url = (
            f"{BASE_URL}/{resource_id}?api-key={API_KEY}&format=json"
            f"&offset={offset}&limit=100&filters%5Bparameters%5D={enc}"
        )
        payload = _curl_json(url)
        batch = payload.get("records", [])
        for rec in batch:
            district = (rec.get("district") or "").strip()
            if not district or district.lower() in ("total", "grand total", "state total"):
                continue
            annual = _to_int(rec.get(annual_field))
            if annual is None:
                continue
            cand = (annual, _monthly_avg(rec, annual))
            # keep the row with the larger annual value on duplicate districts
            if district not in by_district or annual > by_district[district][0]:
                by_district[district] = cand
        total = int(payload.get("total", 0))
        if not batch or offset + len(batch) >= total or len(batch) < 100:
            break
        offset += len(batch)
    return [(d, a, m) for d, (a, m) in by_district.items()]


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest real HMIS district metrics (IPD, stock-out, …).")
    p.add_argument("--only", default=None, help="Comma-separated state names to limit ingestion.")
    p.add_argument("--metrics", default=None,
                   help=f"Comma-separated metric keys (default: all). Options: {','.join(METRICS)}")
    args = p.parse_args()

    states = STATE_RESOURCES
    if args.only:
        want = {s.strip().lower() for s in args.only.split(",")}
        states = {k: v for k, v in STATE_RESOURCES.items() if k.lower() in want}

    metrics = METRICS
    if args.metrics:
        want_m = {m.strip() for m in args.metrics.split(",")}
        metrics = {k: v for k, v in METRICS.items() if k in want_m}

    print(f"→ Ingesting {len(metrics)} HMIS metric(s) for {len(states)} state(s) …", flush=True)
    conn = psycopg2.connect(DATABASE_URL)
    total_written = 0
    try:
        for state, (period, rid) in states.items():
            for metric, candidates in metrics.items():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM district_hmis_metrics "
                        "WHERE state_ut=%s AND period=%s AND metric=%s",
                        (state, period, metric),
                    )
                    if cur.fetchone()[0] > 0:
                        print(f"  {state} ({period}) {metric}: already ingested — skip", flush=True)
                        continue
                try:
                    triples = fetch_state_metric(rid, candidates)
                except Exception as exc:
                    print(f"  ✗ {state}/{metric}: fetch failed ({exc})", file=sys.stderr, flush=True)
                    continue
                if not triples:
                    print(f"  {state} ({period}) {metric}: parameter not found in resource — skip", flush=True)
                    continue
                rows = [(state, d, period, metric, a, m, rid) for (d, a, m) in triples]
                with conn, conn.cursor() as cur:
                    execute_values(
                        cur,
                        """
                        INSERT INTO district_hmis_metrics
                            (state_ut, district, period, metric, annual_value, monthly_avg, resource_id)
                        VALUES %s
                        ON CONFLICT (state_ut, district, period, metric) DO UPDATE SET
                            annual_value = EXCLUDED.annual_value,
                            monthly_avg  = EXCLUDED.monthly_avg,
                            resource_id  = EXCLUDED.resource_id,
                            ingested_at  = NOW()
                        """,
                        rows, page_size=1000,
                    )
                total_written += len(rows)
                print(f"  {state} ({period}) {metric}: {len(rows)} districts", flush=True)
    finally:
        conn.close()
    print(f"✓ Upserted {total_written} district-metric rows from HMIS.")


if __name__ == "__main__":
    main()
