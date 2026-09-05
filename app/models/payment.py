from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"

class PaymentModel(BaseModel):
    payment_id: str  # PAY-YYYYMMDD-XXXX or gateway payment ID
    order_id: str
    amount: float
    currency: str = "INR"
    status: PaymentStatus = PaymentStatus.PENDING
    provider: str = "mock"
    provider_payment_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    verified_at: Optional[datetime] = None
