from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional
from app.config import settings

def get_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.TIMEZONE)
    except Exception:
        return ZoneInfo("Asia/Kolkata")

def utc_now() -> datetime:
    """Always return current time in UTC with timezone awareness."""
    return datetime.now(timezone.utc)

def format_ist(dt: Optional[datetime], fmt: str = "%d %b %Y, %I:%M %p") -> str:
    """Convert UTC datetime to Asia/Kolkata and format as readable string."""
    if not dt:
        return "N/A"
    if dt.tzinfo is None:
        # Treat naive datetime as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    ist_dt = dt.astimezone(get_tz())
    return f"{ist_dt.strftime(fmt)} IST"

def parse_date_ist_str(date_str: str) -> Optional[datetime]:
    """Tolerant parser for common date inputs (YYYY-MM-DD, DD/MM/YYYY, etc.)"""
    date_str = date_str.strip()
    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
    ]
    for f in formats:
        try:
            parsed = datetime.strptime(date_str, f)
            return parsed.replace(tzinfo=get_tz()).astimezone(timezone.utc)
        except ValueError:
            continue
    return None
