from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field

class AdminRole(str, Enum):
    SUPERADMIN = "SUPERADMIN"
    ADMIN = "ADMIN"

class AdminModel(BaseModel):
    phone_number: str
    name: str = "Hostel Admin"
    role: AdminRole = AdminRole.ADMIN
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
