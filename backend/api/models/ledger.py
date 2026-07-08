import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class FacilityBed(Base):
    """Categorised bed matrix (General/ICU/Maternity) per facility."""

    __tablename__ = "facility_beds"

    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), primary_key=True
    )
    bed_type: Mapped[str] = mapped_column(
        Enum("GENERAL", "ICU", "MATERNITY", name="bed_type", create_type=False),
        primary_key=True,
    )
    total_beds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    occupied_beds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Expected date the occupied beds free up (field-entered) — see 013_*.sql.
    occupied_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TestAvailability(Base):
    """Latest daily Yes/No diagnostic-test availability audit per facility."""

    __tablename__ = "test_availability"

    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), primary_key=True
    )
    test_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("diagnostic_tests.id"), primary_key=True
    )
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
