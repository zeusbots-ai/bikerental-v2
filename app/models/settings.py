from datetime import datetime, timezone
from pydantic import BaseModel, Field

class SystemSettingsModel(BaseModel):
    key: str = "system_config"
    is_booking_enabled: bool = True
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: str = "system"
