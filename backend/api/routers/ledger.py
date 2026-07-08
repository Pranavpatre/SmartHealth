"""
ledger.py — Daily digital ledger inputs (Project Pulse Module 1).

Bed Matrix (General/ICU/Maternity) + Test Availability (Yes/No diagnostic audit).

Endpoints
---------
GET  /ledger/beds/{facility_id}   — bed matrix
PUT  /ledger/beds/{facility_id}   — upsert bed matrix (field app)
GET  /ledger/tests/{facility_id}  — test availability checklist
PUT  /ledger/tests/{facility_id}  — upsert test availability
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.rbac import require_role
from db import get_db
from models.ledger import FacilityBed, TestAvailability

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/ledger")

_staff_plus = require_role(
    "FIELD_WORKER", "PHC_ADMIN", "DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN"
)
_BED_TYPES = ("GENERAL", "ICU", "MATERNITY")


# ── Bed matrix ───────────────────────────────────────────────────────────────

class BedRow(BaseModel):
    bed_type: str
    total_beds: int
    occupied_beds: int
    occupied_until: date | None = None  # expected date the occupied beds free up


class BedMatrix(BaseModel):
    facility_id: uuid.UUID
    beds: list[BedRow]
    updated_at: datetime | None = None


@router.get("/beds/{facility_id}", response_model=BedMatrix)
async def get_beds(
    facility_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_staff_plus),
) -> BedMatrix:
    rows = (
        await db.execute(
            select(FacilityBed).where(FacilityBed.facility_id == facility_id)
        )
    ).scalars().all()
    by_type = {r.bed_type: r for r in rows}
    beds = [
        BedRow(
            bed_type=bt,
            total_beds=by_type[bt].total_beds if bt in by_type else 0,
            occupied_beds=by_type[bt].occupied_beds if bt in by_type else 0,
            occupied_until=by_type[bt].occupied_until if bt in by_type else None,
        )
        for bt in _BED_TYPES
    ]
    updated = max((r.updated_at for r in rows), default=None)
    return BedMatrix(facility_id=facility_id, beds=beds, updated_at=updated)


@router.put("/beds/{facility_id}", response_model=BedMatrix)
async def put_beds(
    facility_id: uuid.UUID,
    body: list[BedRow],
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_staff_plus),
) -> BedMatrix:
    for row in body:
        if row.bed_type not in _BED_TYPES:
            continue
        await db.execute(
            sa_text(
                """
                INSERT INTO facility_beds (facility_id, bed_type, total_beds, occupied_beds, occupied_until, updated_at)
                VALUES (:fid, :bt, :tot, :occ, :until, NOW())
                ON CONFLICT (facility_id, bed_type) DO UPDATE SET
                    total_beds = EXCLUDED.total_beds,
                    occupied_beds = EXCLUDED.occupied_beds,
                    occupied_until = EXCLUDED.occupied_until,
                    updated_at = NOW()
                """
            ),
            {"fid": str(facility_id), "bt": row.bed_type,
             "tot": max(row.total_beds, 0), "occ": max(row.occupied_beds, 0),
             "until": row.occupied_until},
        )
    log.info("beds_updated", facility_id=str(facility_id), user_id=str(current_user.id))
    return await get_beds(facility_id, db, current_user)


# ── Test availability ────────────────────────────────────────────────────────

class TestRow(BaseModel):
    test_id: int
    test_name: str | None = None
    available: bool


class TestChecklist(BaseModel):
    facility_id: uuid.UUID
    tests: list[TestRow]


@router.get("/tests/{facility_id}", response_model=TestChecklist)
async def get_tests(
    facility_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_staff_plus),
) -> TestChecklist:
    # All catalogue tests LEFT JOIN this facility's latest availability (default available).
    rows = (
        await db.execute(
            sa_text(
                """
                SELECT dt.id AS test_id, dt.name AS test_name,
                       COALESCE(ta.available, TRUE) AS available
                FROM diagnostic_tests dt
                LEFT JOIN test_availability ta
                       ON ta.test_id = dt.id AND ta.facility_id = :fid
                ORDER BY dt.name
                """
            ),
            {"fid": str(facility_id)},
        )
    ).fetchall()
    return TestChecklist(
        facility_id=facility_id,
        tests=[TestRow(test_id=r.test_id, test_name=r.test_name, available=r.available) for r in rows],
    )


class FootfallTally(BaseModel):
    general: int = 0     # OPD
    maternal: int = 0    # IPD / maternity
    emergency: int = 0


@router.get("/footfall/{facility_id}", response_model=FootfallTally)
async def get_footfall(
    facility_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_staff_plus),
) -> FootfallTally:
    """Today's footfall tally so far, from the latest daily_snapshot row."""
    result = await db.execute(
        sa_text(
            """
            SELECT opd_count, ipd_count, emergency_count
            FROM daily_snapshots
            WHERE facility_id = :fid AND time::date = CURRENT_DATE
            ORDER BY time DESC
            LIMIT 1
            """
        ),
        {"fid": str(facility_id)},
    )
    row = result.first()
    if not row:
        return FootfallTally()
    return FootfallTally(general=row.opd_count or 0, maternal=row.ipd_count or 0, emergency=row.emergency_count or 0)


@router.put("/footfall/{facility_id}", status_code=status.HTTP_201_CREATED)
async def put_footfall(
    facility_id: uuid.UUID,
    body: FootfallTally,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_staff_plus),
) -> FootfallTally:
    """Record today's footfall tally (general/maternal/emergency) as a new
    daily snapshot, carrying forward the latest staffing/bed values so the
    health scorer stays consistent."""
    await db.execute(
        sa_text(
            """
            INSERT INTO daily_snapshots
                (time, facility_id, opd_count, ipd_count, emergency_count,
                 beds_occupied, doctors_present, doctors_rostered, input_channel)
            SELECT NOW(), :fid, :opd, :ipd, :emerg,
                COALESCE((SELECT beds_occupied FROM daily_snapshots
                          WHERE facility_id = :fid ORDER BY time DESC LIMIT 1), 0),
                COALESCE((SELECT doctors_present FROM daily_snapshots
                          WHERE facility_id = :fid ORDER BY time DESC LIMIT 1), 2),
                COALESCE((SELECT doctors_rostered FROM daily_snapshots
                          WHERE facility_id = :fid ORDER BY time DESC LIMIT 1), 2),
                'app'
            """
        ),
        {"fid": str(facility_id), "opd": max(body.general, 0),
         "ipd": max(body.maternal, 0), "emerg": max(body.emergency, 0)},
    )
    log.info("footfall_recorded", facility_id=str(facility_id), user_id=str(current_user.id),
             general=body.general, maternal=body.maternal, emergency=body.emergency)
    return body


@router.put("/tests/{facility_id}", response_model=TestChecklist)
async def put_tests(
    facility_id: uuid.UUID,
    body: list[TestRow],
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_staff_plus),
) -> TestChecklist:
    for row in body:
        await db.execute(
            sa_text(
                """
                INSERT INTO test_availability (facility_id, test_id, available, checked_at)
                VALUES (:fid, :tid, :avail, NOW())
                ON CONFLICT (facility_id, test_id) DO UPDATE SET
                    available = EXCLUDED.available, checked_at = NOW()
                """
            ),
            {"fid": str(facility_id), "tid": row.test_id, "avail": row.available},
        )
    log.info("tests_updated", facility_id=str(facility_id), user_id=str(current_user.id))
    return await get_tests(facility_id, db, current_user)
