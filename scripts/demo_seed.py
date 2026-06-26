#!/usr/bin/env python3
"""
SmartHealth — Demo Seed Script
Sets up the 3-minute demo scenario:
  PHC-01 (Shirur) is 2 days from insulin stockout.
  PHC-08 (Haveli) has 85 vials of surplus insulin.
  A redistribution plan + alert are pre-created for the demo.
"""

import os
import sys
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://smarthealth:smarthealth@localhost:5432/smarthealth",
)

FACILITY_SHIRUR = "MH-PUNE-PHC-01"   # critically low insulin
FACILITY_HAVELI = "MH-PUNE-PHC-08"   # surplus donor
INSULIN_NAME    = "Insulin Regular 100IU"
DISTRICT_CODE   = "MH-PUNE"


def connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def set_insulin_stock(cur, facility_code: str, quantity: int) -> None:
    """Replace all stock_batches for facility+insulin with a single fresh batch."""
    # Resolve IDs
    cur.execute("SELECT id FROM facilities WHERE code = %s", (facility_code,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Facility not found: {facility_code}")
    facility_id = row["id"]

    cur.execute("SELECT id FROM medicines WHERE name = %s", (INSULIN_NAME,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Medicine not found: {INSULIN_NAME}")
    medicine_id = row["id"]

    # Clear existing batches
    cur.execute(
        "DELETE FROM stock_batches WHERE facility_id = %s AND medicine_id = %s",
        (facility_id, medicine_id),
    )

    expiry = date.today() + timedelta(days=180)
    batch_number = f"DEMO-{facility_code}-INSULIN"
    cur.execute(
        """
        INSERT INTO stock_batches (facility_id, medicine_id, batch_number, quantity, expiry_date)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (facility_id, medicine_id, batch_number, quantity, expiry),
    )
    print(f"  [stock]  {facility_code}: {quantity} vials of {INSULIN_NAME}  (batch {batch_number})")


def create_redistribution_plan(cur) -> str:
    """Create (or reuse) a PENDING redistribution plan for Pune district."""
    cur.execute("SELECT id FROM districts WHERE code = %s", (DISTRICT_CODE,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"District not found: {DISTRICT_CODE}")
    district_id = row["id"]

    # Check for an existing PENDING plan to avoid duplicates on re-runs
    cur.execute(
        """
        SELECT id FROM redistribution_plans
        WHERE district_id = %s AND status = 'PENDING'
        ORDER BY generated_at DESC
        LIMIT 1
        """,
        (district_id,),
    )
    existing = cur.fetchone()
    if existing:
        plan_id = str(existing["id"])
        print(f"  [plan]   Reusing existing PENDING redistribution plan  id={plan_id}")
        return plan_id

    cur.execute(
        """
        INSERT INTO redistribution_plans (district_id, status, total_savings, notes)
        VALUES (%s, 'PENDING', 18000.00, 'Demo: Shirur insulin crisis — transfer from Haveli')
        RETURNING id
        """,
        (district_id,),
    )
    plan_id = str(cur.fetchone()["id"])
    print(f"  [plan]   Created redistribution plan  id={plan_id}")
    return plan_id


def create_redistribution_item(cur, plan_id: str) -> None:
    """Add the Haveli → Shirur insulin transfer item to the plan."""
    cur.execute("SELECT id FROM facilities WHERE code = %s", (FACILITY_SHIRUR,))
    shirur_id = str(cur.fetchone()["id"])

    cur.execute("SELECT id FROM facilities WHERE code = %s", (FACILITY_HAVELI,))
    haveli_id = str(cur.fetchone()["id"])

    cur.execute("SELECT id FROM medicines WHERE name = %s", (INSULIN_NAME,))
    medicine_id = cur.fetchone()["id"]

    # Avoid duplicate items on re-runs
    cur.execute(
        """
        SELECT id FROM redistribution_items
        WHERE plan_id = %s AND from_facility = %s AND to_facility = %s AND medicine_id = %s
        """,
        (plan_id, haveli_id, shirur_id, medicine_id),
    )
    if cur.fetchone():
        print("  [item]   Redistribution item already exists, skipping")
        return

    cur.execute(
        """
        INSERT INTO redistribution_items
            (plan_id, medicine_id, from_facility, to_facility,
             quantity, distance_km, estimated_cost, estimated_saving, status)
        VALUES (%s, %s, %s, %s, 60, 34.00, 2000.00, 18000.00, 'PENDING')
        """,
        (plan_id, medicine_id, haveli_id, shirur_id),
    )
    print("  [item]   Haveli → Shirur: 60 vials  (34 km, saves ₹18,000)")


def create_alert(cur) -> str:
    """Create (or reuse) a CRITICAL/OPEN alert for Shirur insulin stockout."""
    cur.execute("SELECT id FROM facilities WHERE code = %s", (FACILITY_SHIRUR,))
    shirur_id = str(cur.fetchone()["id"])

    title = "Stockout risk: Insulin Regular 100IU"

    # Avoid duplicate alerts on re-runs
    cur.execute(
        """
        SELECT id FROM alerts
        WHERE facility_id = %s AND title = %s AND status = 'OPEN'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (shirur_id, title),
    )
    existing = cur.fetchone()
    if existing:
        alert_id = str(existing["id"])
        print(f"  [alert]  Reusing existing OPEN alert  id={alert_id}")
        return alert_id

    body = (
        "Shirur PHC: Insulin Regular 100IU runs out in 2 days. "
        "Confidence: 91%. Transfer from Haveli PHC available."
    )
    cur.execute(
        """
        INSERT INTO alerts (facility_id, severity, status, title, body)
        VALUES (%s, 'CRITICAL', 'OPEN', %s, %s)
        RETURNING id
        """,
        (shirur_id, title, body),
    )
    alert_id = str(cur.fetchone()["id"])
    print(f"  [alert]  Created CRITICAL alert for Shirur PHC  id={alert_id}")
    return alert_id


def insert_voice_snapshot(cur) -> None:
    """Insert today's daily_snapshot for PHC-01 to demo voice entry (opd_count=180)."""
    cur.execute("SELECT id FROM facilities WHERE code = %s", (FACILITY_SHIRUR,))
    shirur_id = str(cur.fetchone()["id"])

    today = date.today()
    cur.execute(
        """
        SELECT 1 FROM daily_snapshots
        WHERE facility_id = %s AND time::date = %s
        LIMIT 1
        """,
        (shirur_id, today),
    )
    if cur.fetchone():
        print("  [snap]   Today's daily_snapshot for PHC-01 already exists, skipping")
        return

    cur.execute(
        """
        INSERT INTO daily_snapshots
            (time, facility_id, opd_count, ipd_count, beds_occupied,
             doctors_present, doctors_rostered, input_channel, notes)
        VALUES (NOW(), %s, 180, 3, 8, 2, 2, 'voice',
                'Demo voice entry — PHC-01 Shirur footfall today')
        """,
        (shirur_id,),
    )
    print("  [snap]   Inserted voice daily_snapshot for PHC-01  (opd_count=180)")


def print_walkthrough(plan_id: str, alert_id: str) -> None:
    print()
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║           SmartHealth — 3-Minute Demo Ready                  ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("Scenario: PHC-01 (Shirur) is 2 days from insulin stockout.")
    print("          PHC-08 (Haveli) has 85 vials of surplus insulin.")
    print()
    print("Step 1: Open dashboard → http://localhost:3000")
    print("        PHC-01 marker will be RED 🔴")
    print()
    print("Step 2: Click on PHC-01 alert:")
    print('        "Insulin runs out in 2 days. Confidence: 91%"')
    print()
    print("Step 3: Review redistribution plan:")
    print("        Transfer 60 units Haveli → Shirur (distance: 34km, saves ₹18,000)")
    print()
    print(f"Step 4: Click [Approve] or:")
    print(f"        curl -X POST localhost:8000/api/v1/redistribution/plans/{plan_id}/approve \\")
    print( "             -H \"Authorization: Bearer {token}\"")
    print()
    print("Step 5: PHC-01 score updates: 🔴 → 🟡")
    print("        WhatsApp sent to Dr. Anand Kulkarni (+919876543210)")
    print()
    print("API Docs: http://localhost:8000/docs")
    print()
    print("─" * 64)
    print(f"  plan_id  = {plan_id}")
    print(f"  alert_id = {alert_id}")
    print("─" * 64)
    print()


def main():
    print()
    print("SmartHealth — seeding demo data...")
    print()

    try:
        conn = connect()
    except Exception as exc:
        print(f"ERROR: Could not connect to database: {exc}", file=sys.stderr)
        print(f"       DATABASE_URL = {DATABASE_URL}", file=sys.stderr)
        sys.exit(1)

    try:
        with conn:
            with conn.cursor() as cur:
                # a) PHC-01 Shirur: 8 vials (critically low)
                set_insulin_stock(cur, FACILITY_SHIRUR, 8)

                # b) PHC-08 Haveli: 85 vials (surplus donor)
                set_insulin_stock(cur, FACILITY_HAVELI, 85)

                # c) Redistribution plan for Pune district
                plan_id = create_redistribution_plan(cur)

                # d) Redistribution item: Haveli → Shirur, 60 vials
                create_redistribution_item(cur, plan_id)

                # e) Alert for PHC-01 stockout risk
                alert_id = create_alert(cur)

                # f) Today's daily_snapshot for PHC-01 (voice entry demo)
                insert_voice_snapshot(cur)

    except Exception as exc:
        print(f"\nERROR during seeding: {exc}", file=sys.stderr)
        conn.rollback()
        conn.close()
        sys.exit(1)

    conn.close()
    print()
    print("All demo data committed successfully.")

    print_walkthrough(plan_id, alert_id)


if __name__ == "__main__":
    main()
