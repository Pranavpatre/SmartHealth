from typing import Optional
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class RedistributionPlan(Base):
    __tablename__ = "redistribution_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Matches transfer_status ENUM: PENDING | APPROVED | DEFERRED | IN_TRANSIT | COMPLETED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    # Estimated INR savings — NUMERIC(12,2) in schema
    total_savings: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    items: Mapped[list["RedistributionItem"]] = relationship(
        "RedistributionItem", back_populates="plan", cascade="all, delete-orphan"
    )


class RedistributionItem(Base):
    """Maps to redistribution_items table in schema."""

    __tablename__ = "redistribution_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("redistribution_plans.id"), nullable=False
    )
    medicine_id: Mapped[Optional[int]] = mapped_column(ForeignKey("medicines.id"), nullable=True)
    test_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("diagnostic_tests.id"), nullable=True
    )
    from_facility: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    to_facility: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # NUMERIC(6,2) in schema
    distance_km: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    # NUMERIC(10,2) in schema
    estimated_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    estimated_saving: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    # Matches transfer_status ENUM
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    trigger_prediction: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_predictions.id"), nullable=True
    )

    plan: Mapped[RedistributionPlan] = relationship(
        "RedistributionPlan", back_populates="items"
    )
