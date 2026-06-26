from typing import Optional
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class AIPrediction(Base):
    __tablename__ = "ai_predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    # Nullable — NULL for footfall/anomaly prediction types
    medicine_id: Mapped[Optional[int]] = mapped_column(ForeignKey("medicines.id"), nullable=True)
    test_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("diagnostic_tests.id"), nullable=True
    )
    # Matches prediction_type ENUM: STOCKOUT | FOOTFALL | DIAGNOSTIC_SHORTAGE | ANOMALY | HEALTH_SCORE
    prediction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # NUMERIC(10,2) — days until stockout, expected patients, etc.
    predicted_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    # NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3), nullable=True)
    # JSONB: {"monsoon": 0.20, "dengue_trend": 0.15, ...}
    reasoning: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    recommendation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Filled post-hoc for retraining
    actual_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    # correct | wrong | partial
    worker_feedback: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    feedback_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
