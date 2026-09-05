from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class OrderStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    COMPLETED = "COMPLETED"

class DurationType(str, Enum):
    HOURLY = "HOURLY"
    DAILY = "DAILY"

class OrderModel(BaseModel):
    order_id: str  # ORD-YYYYMMDD-XXXX
    user_phone: str
    vehicle_id: str
    rental_date: str
    duration_type: DurationType
    duration_value: float
    total_amount: float
    status: OrderStatus = OrderStatus.PENDING_PAYMENT
    hold_expires_at: datetime
    payment_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
