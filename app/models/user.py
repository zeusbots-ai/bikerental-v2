from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class UserState(str, Enum):
    IDLE = "IDLE"
    AWAITING_STUDENT_CONFIRMATION = "AWAITING_STUDENT_CONFIRMATION"
    AWAITING_ID_CARD = "AWAITING_ID_CARD"
    PENDING_ADMIN_REVIEW = "PENDING_ADMIN_REVIEW"
    SELECTING_VEHICLE = "SELECTING_VEHICLE"
    SELECTING_DATE = "SELECTING_DATE"
    SELECTING_DURATION_TYPE = "SELECTING_DURATION_TYPE"
    SELECTING_DURATION_VALUE = "SELECTING_DURATION_VALUE"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"

class VerificationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class UserModel(BaseModel):
    phone_number: str
    name: Optional[str] = None
    is_cuap_student: bool = False
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    current_verification_id: Optional[str] = None
    state: UserState = UserState.IDLE
    temp_booking: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
