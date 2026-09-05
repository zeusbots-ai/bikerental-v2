import logging
from typing import List, Optional, Dict, Any
from app.config import settings, normalize_phone
from app.database import get_database
from app.services.whatsapp.base import WhatsAppClientInterface
from app.services.whatsapp.bridge_client import WhatsAppBridgeClient
from app.services.whatsapp.cloud_client import MetaWhatsAppCloudClient

logger = logging.getLogger(__name__)

async def _get_admin_targets() -> List[str]:
    """
    Resolves the list of admin destinations strictly based on settings.admin_phone_list.
    Only configured admin phones (e.g. 916371737949) are targeted.
    Prioritizes stored WhatsApp JIDs (such as privacy @lid addresses) over plain phone numbers
    to avoid delivery failures.
    """
    db = get_database()
    raw_targets = [normalize_phone(p) for p in settings.admin_phone_list if p.strip()]

    resolved = []
    if db is not None:
        for t in raw_targets:
            if "@" in t:
                resolved.append(t)
            else:
                try:
                    norm = normalize_phone(t)
                    candidates = list({c for c in [norm, t, norm[2:] if norm.startswith("91") else None] if c})
                    admin_doc = await db.admins.find_one({"phone_number": {"$in": candidates}, "is_active": True})
                    if admin_doc and admin_doc.get("wa_jid"):
                        resolved.append(admin_doc["wa_jid"])
                        continue
                    user_doc = await db.users.find_one({"phone_number": {"$in": candidates}})
                    if user_doc and user_doc.get("wa_jid"):
                        resolved.append(user_doc["wa_jid"])
                        continue
                except Exception as e:
                    logger.debug(f"[WhatsAppService] wa_jid lookup error for {t}: {e}")
                resolved.append(normalize_phone(t))
    else:
        resolved = [normalize_phone(t) for t in raw_targets]

    seen = set()
    deduped = []
    for r in resolved:
        if r and r not in seen:
            seen.add(r)
            deduped.append(r)
    return deduped


class WhatsAppService:
    def __init__(self):
        if settings.WHATSAPP_PROVIDER == "cloud":
            logger.info("Initializing Meta WhatsApp Cloud Client...")
            self.client: WhatsAppClientInterface = MetaWhatsAppCloudClient()
        else:
            logger.info("Initializing Local WhatsApp Web Bridge Client...")
            self.client: WhatsAppClientInterface = WhatsAppBridgeClient(settings.BRIDGE_URL)

    async def send_message(self, to: str, message: str) -> bool:
        return await self.client.send_text_message(to, message)

    async def send_media(
        self,
        to: str,
        file_path: str,
        caption: str = "",
        mime_type: Optional[str] = None
    ) -> bool:
        return await self.client.send_media_message(to, file_path, caption, mime_type)

    async def notify_admins(
        self,
        message: str,
        file_path: Optional[str] = None,
        mime_type: Optional[str] = None
    ) -> int:
        """
        Sends notification message (and optional media) to all configured/active admins.
        Returns the count of successfully sent admin notifications.
        """
        admin_targets = await _get_admin_targets()

        success_count = 0
        for target in admin_targets:
            try:
                sent = False
                if file_path:
                    sent = await self.send_media(target, file_path, caption=message, mime_type=mime_type)
                    if not sent:
                        logger.warning(f"[WhatsAppService] send_media returned False for admin {target}; falling back to text alert")
                        sent = await self.send_message(target, message)
                else:
                    sent = await self.send_message(target, message)
                if sent:
                    success_count += 1
            except Exception as e:
                logger.error(f"[WhatsAppService] Failed to notify admin {target}: {e}")
                # Fallback to text notification if sending media threw an exception
                if file_path:
                    try:
                        logger.info(f"[WhatsAppService] Retrying notification for admin {target} via text alert...")
                        if await self.send_message(target, message):
                            success_count += 1
                    except Exception as fallback_e:
                        logger.error(f"[WhatsAppService] Text fallback also failed for admin {target}: {fallback_e}")

        return success_count

    async def forward_message(self, message_id: str, to: str) -> bool:
        return await self.client.forward_message(message_id, to)

    async def forward_to_admins(self, message_id: str) -> int:
        """
        Directly forwards an original WhatsApp message to all admins.
        Preserves 100% original full image resolution without compression.
        """
        admin_targets = await _get_admin_targets()

        success_count = 0
        for target in admin_targets:
            try:
                if await self.forward_message(message_id, target):
                    success_count += 1
            except Exception as e:
                logger.warning(f"[WhatsAppService] Failed to forward message {message_id} to {target}: {e}")
        return success_count

    async def get_status(self) -> Dict[str, Any]:
        return await self.client.get_status()

    async def get_qr_data(self) -> Dict[str, Any]:
        if isinstance(self.client, WhatsAppBridgeClient):
            return await self.client.get_qr_data()
        return {"status": "READY", "raw": None, "dataUrl": None}

whatsapp_service = WhatsAppService()
