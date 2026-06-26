from typing import Optional
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class Medicine(Base):
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Nullable in schema — no NOT NULL constraint
    generic_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Matches medicine_category ENUM
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # tablets, vials, strips, kits — max 20 chars per schema
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="units")
    reorder_level: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # No created_at in schema


class StockBatch(Base):
    """FEFO stock batches — maps to stock_batches table in schema."""

    __tablename__ = "stock_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id"), nullable=False)
    # Nullable in schema — no NOT NULL constraint
    batch_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    received_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class DiagnosticTest(Base):
    __tablename__ = "diagnostic_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Nullable in schema — no NOT NULL constraint
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Default 'tests' per schema
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="tests")
    reorder_level: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
