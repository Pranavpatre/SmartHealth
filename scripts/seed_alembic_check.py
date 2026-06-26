#!/usr/bin/env python3
"""
SmartHealth — Alembic & Schema Sanity Check
Verifies that migrations have run and key tables exist.
Exits 0 if all checks pass, 1 if any fail.
"""

import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://smarthealth:smarthealth@localhost:5432/smarthealth",
)

KEY_TABLES = [
    "facilities",
    "medicines",
    "users",
    "alerts",
    "stock_batches",
    "redistribution_plans",
    "redistribution_items",
    "daily_snapshots",
    "ai_predictions",
]

PASS = "PASS"
FAIL = "FAIL"


def run_checks():
    print()
    print("SmartHealth — Alembic & Schema Sanity Check")
    print("=" * 50)

    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    except Exception as exc:
        print(f"\n  {FAIL}  Cannot connect to database: {exc}")
        print(f"         DATABASE_URL = {DATABASE_URL}")
        sys.exit(1)

    results = []

    with conn:
        with conn.cursor() as cur:
            # ── Check 1: alembic_version table exists ──────────────────────
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND   table_name   = 'alembic_version'
                ) AS exists
                """
            )
            alembic_table_exists = cur.fetchone()["exists"]
            status = PASS if alembic_table_exists else FAIL
            results.append(status == PASS)
            print(f"  [{status}]  alembic_version table exists")

            # ── Check 2: alembic_version has at least one row ──────────────
            if alembic_table_exists:
                cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
                row = cur.fetchone()
                if row:
                    status = PASS
                    version = row["version_num"]
                else:
                    status = FAIL
                    version = "(no rows)"
                results.append(status == PASS)
                print(f"  [{status}]  alembic_version has a migration row  (version: {version})")
            else:
                results.append(False)
                print(f"  [{FAIL}]  alembic_version has a migration row  (skipped — table missing)")

            # ── Check 3+: key application tables exist ─────────────────────
            for table in KEY_TABLES:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND   table_name   = %s
                    ) AS exists
                    """,
                    (table,),
                )
                exists = cur.fetchone()["exists"]
                status = PASS if exists else FAIL
                results.append(exists)
                print(f"  [{status}]  table '{table}' exists")

    conn.close()

    total  = len(results)
    passed = sum(results)
    failed = total - passed

    print()
    print("─" * 50)
    print(f"  {passed}/{total} checks passed", end="")
    if failed:
        print(f"  |  {failed} FAILED")
    else:
        print("  — all good!")
    print()

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    run_checks()
