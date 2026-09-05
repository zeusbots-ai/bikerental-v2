from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class VehicleType(str, Enum):
    SCOOTY = "SCOOTY"
    BIKE = "BIKE"

class VehicleStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    HELD = "HELD"
    BOOKED = "BOOKED"
    RENTED = "RENTED"
    MAINTENANCE = "MAINTENANCE"

class VehicleModel(BaseModel):
    vehicle_id: str
    name: str
    type: VehicleType = VehicleType.SCOOTY
    registration_number: str
    description: str = ""
    photos: List[str] = Field(default_factory=list)
    price_per_hour: float
    price_per_day: float
    availability_status: VehicleStatus = VehicleStatus.AVAILABLE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
