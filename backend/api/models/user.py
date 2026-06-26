import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

if TYPE_CHECKING:
    from models.facility import Facility, District


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    facility_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=True
    )
    district_id: Mapped[Optional[int]] = mapped_column(ForeignKey("districts.id"), nullable=True)
    # Matches user_role ENUM: FIELD_WORKER | PHC_ADMIN | DISTRICT_OFFICER | STATE_ADMIN | SUPERADMIN
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(15), nullable=False, unique=True)
    language_pref: Mapped[str] = mapped_column(String(10), nullable=False, default="hi")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
