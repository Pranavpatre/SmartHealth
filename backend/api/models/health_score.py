from typing import Optional
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class FacilityHealthScore(Base):
    """
    TimescaleDB hypertable — partitioned on `time`.
    No single-column UUID primary key; composite (time, facility_id) is the natural key.
    A surrogate UUID pk is added here so SQLAlchemy ORM can address rows unambiguously.
    """

    __tablename__ = "facility_health_scores"

    # TimescaleDB hypertable; time is the partitioning column
    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False, primary_key=True
    )
    # Component scores 0-100, weight noted per schema comments
    medicine_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # weight 25%
    doctor_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # weight 20%
    bed_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # weight 20%
    wait_time_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # weight 20%
    diagnostics_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # weight 15%
    # Weighted composite of the above
    overall_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    # GREEN | YELLOW | RED
    status: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
