from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class VerificationModel(BaseModel):
    verification_id: str  # VER-YYYYMMDD-XXXX
    user_phone: str
    id_card_media_path: str
    status: VerificationStatus = VerificationStatus.PENDING
    reviewed_by: Optional[str] = None
    review_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
