import logging
import httpx
from typing import Dict, Any, Optional
from app.config import settings
from app.database import get_database
from app.services.whatsapp.base import WhatsAppClientInterface

logger = logging.getLogger(__name__)

async def _resolve_target(to: str) -> str:
    """
    'to' is usually a plain phone number. If we've previously stored the
    real WhatsApp JID for this number (e.g. it's a @lid contact rather than
    @c.us), use that exact JID instead of guessing one from digits.
    """
    clean_to = "".join(filter(str.isdigit, str(to)))
    if "@" in str(to):
        return str(to)  # already a full JID, use as-is
    try:
        db = get_database()
        if db is not None:
            user = await db.users.find_one({"phone_number": clean_to}, {"wa_jid": 1})
            if user and user.get("wa_jid"):
                return user["wa_jid"]
    except Exception as e:
        logger.error(f"[BridgeClient] Failed to look up wa_jid for {clean_to}: {e}")
    return clean_to

class WhatsAppBridgeClient(WhatsAppClientInterface):
    """
    Communicates with the local Node.js whatsapp-web.js bridge microservice.
    """

    def __init__(self, base_url: str = None):
        self.base_url = (base_url or settings.BRIDGE_URL).rstrip("/")
        self.client = httpx.AsyncClient(timeout=30.0)

    async def send_text_message(self, to: str, message: str) -> bool:
        clean_to = await _resolve_target(to)
        url = f"{self.base_url}/send-message"
        try:
            resp = await self.client.post(url, json={"to": clean_to, "message": message})
            if resp.status_code == 200:
                return True
            logger.error(f"[BridgeClient] Failed to send message to {clean_to}. Status: {resp.status_code}, Body: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"[BridgeClient] Exception sending message to {clean_to}: {e}")
            return False

    async def send_media_message(
        self,
        to: str,
        file_path: str,
        caption: str = "",
        mime_type: Optional[str] = None
    ) -> bool:
        clean_to = await _resolve_target(to)
        url = f"{self.base_url}/send-media"
        payload = {
            "to": clean_to,
            "filePath": file_path,
            "caption": caption,
            "mimetype": mime_type
        }
        try:
            resp = await self.client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            logger.error(f"[BridgeClient] Failed to send media to {clean_to}. Status: {resp.status_code}, Body: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"[BridgeClient] Exception sending media to {clean_to}: {e}")
            return False

    async def forward_message(self, message_id: str, to: str) -> bool:
        clean_to = await _resolve_target(to)
        url = f"{self.base_url}/forward-message"
        try:
            resp = await self.client.post(url, json={"message_id": message_id, "to": clean_to})
            if resp.status_code == 200:
                logger.info(f"[BridgeClient] Successfully forwarded message {message_id} to {clean_to}")
                return True
            logger.warning(f"[BridgeClient] Failed to forward message {message_id} to {clean_to}. Status: {resp.status_code}, Body: {resp.text}")
            return False
        except Exception as e:
            logger.warning(f"[BridgeClient] Exception forwarding message {message_id} to {clean_to}: {e}")
            return False

    async def get_status(self) -> Dict[str, Any]:
        url = f"{self.base_url}/status"
        try:
            resp = await self.client.get(url, timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
            return {"status": "ERROR", "statusCode": resp.status_code}
        except Exception as e:
            return {"status": "UNREACHABLE", "error": str(e)}

    async def get_qr_data(self) -> Dict[str, Any]:
        url = f"{self.base_url}/qr-data"
        try:
            resp = await self.client.get(url, timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
            return {"status": "ERROR", "raw": None, "dataUrl": None}
        except Exception as e:
            return {"status": "UNREACHABLE", "error": str(e), "raw": None, "dataUrl": None}

    async def close(self):
        await self.client.aclose()
