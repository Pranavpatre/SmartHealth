from typing import Optional
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    prediction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_predictions.id"), nullable=True
    )
    # Matches alert_severity ENUM: INFO | WARNING | CRITICAL
    # Matches alert_severity ENUM: INFO | WARNING | CRITICAL
    severity: Mapped[str] = mapped_column(
        Enum("INFO", "WARNING", "CRITICAL", name="alert_severity", create_type=False),
        nullable=False,
    )
    # Matches alert_status ENUM: OPEN | ACKNOWLEDGED | RESOLVED | SNOOZED
    status: Mapped[str] = mapped_column(
        Enum("OPEN", "ACKNOWLEDGED", "RESOLVED", "SNOOZED", name="alert_status", create_type=False),
        nullable=False,
        default="OPEN",
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured, translatable alert. title/body remain the English fallback
    # (and feed the WhatsApp/SMS path); the dashboard renders message_key with
    # message_params via i18n so the alert text localizes.
    message_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_params: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Notification(Base):
    """Notification delivery log — maps to notifications table in schema."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # whatsapp | sms | push | in_app
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    # BCP-47 language code
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # YES | NO | DEFER for WhatsApp action responses
    response: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
