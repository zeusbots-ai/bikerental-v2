from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

class AuditLogModel(BaseModel):
    log_id: str
    admin_phone: str
    action: str
    target_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
