import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class State(Base):
    __tablename__ = "states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # e.g. MH, TN, KA — max 5 chars per schema
    code: Mapped[str] = mapped_column(String(5), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    districts: Mapped[list["District"]] = relationship("District", back_populates="state")


class District(Base):
    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state_id: Mapped[int] = mapped_column(ForeignKey("states.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    state: Mapped[State] = relationship("State", back_populates="districts")
    facilities: Mapped[list["Facility"]] = relationship("Facility", back_populates="district")


class Facility(Base):
    __tablename__ = "facilities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"), nullable=False)
    # e.g. MH-PUNE-PHC-021 — max 20 chars per schema
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    # ABDM Health Facility Registry (HFR) id — links this facility to the
    # national registry. Populated later via ABDM sandbox/production (Phase 3).
    hfr_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Matches facility_type ENUM: PHC | CHC | SUB_CENTRE | DISTRICT_HOSPITAL
    facility_type: Mapped[str] = mapped_column(
        Enum("PHC", "CHC", "SUB_CENTRE", "DISTRICT_HOSPITAL", name="facility_type", create_type=False),
        nullable=False,
    )
    # PostGIS Point geometry, SRID 4326
    location: Mapped[Optional[Geometry]] = mapped_column(
        Geometry("POINT", srid=4326), nullable=True
    )
    address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bed_capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    district: Mapped[District] = relationship("District", back_populates="facilities")
