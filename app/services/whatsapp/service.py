import logging
from typing import List, Optional, Dict, Any
from app.config import settings
from app.database import get_database
from app.services.whatsapp.base import WhatsAppClientInterface
from app.services.whatsapp.bridge_client import WhatsAppBridgeClient
from app.services.whatsapp.cloud_client import MetaWhatsAppCloudClient

logger = logging.getLogger(__name__)

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
        db = get_database()
        admin_phones = set(settings.admin_phone_list)

        if db is not None:
            try:
                cursor = db.admins.find({"is_active": True})
                async for admin in cursor:
                    phone = admin.get("phone_number")
                    if phone:
                        admin_phones.add(phone.strip().replace("+", ""))
            except Exception as e:
                logger.error(f"[WhatsAppService] Error fetching admins from DB: {e}")

        success_count = 0
        for phone in admin_phones:
            try:
                sent = False
                if file_path:
                    sent = await self.send_media(phone, file_path, caption=message, mime_type=mime_type)
                    if not sent:
                        logger.warning(f"[WhatsAppService] send_media returned False for admin {phone}; falling back to text alert")
                        sent = await self.send_message(phone, message)
                else:
                    sent = await self.send_message(phone, message)
                if sent:
                    success_count += 1
            except Exception as e:
                logger.error(f"[WhatsAppService] Failed to notify admin {phone}: {e}")
                # Fallback to text notification if sending media threw an exception
                if file_path:
                    try:
                        logger.info(f"[WhatsAppService] Retrying notification for admin {phone} via text alert...")
                        if await self.send_message(phone, message):
                            success_count += 1
                    except Exception as fallback_e:
                        logger.error(f"[WhatsAppService] Text fallback also failed for admin {phone}: {fallback_e}")

        return success_count

    async def forward_message(self, message_id: str, to: str) -> bool:
        return await self.client.forward_message(message_id, to)

    async def forward_to_admins(self, message_id: str) -> int:
        """
        Directly forwards an original WhatsApp message to all admins.
        Preserves 100% original full image resolution without compression.
        """
        db = get_database()
        admin_phones = set(settings.admin_phone_list)
        if db is not None:
            try:
                cursor = db.admins.find({"is_active": True})
                async for admin in cursor:
                    phone = admin.get("phone_number")
                    if phone:
                        admin_phones.add(phone.strip().replace("+", ""))
            except Exception as e:
                logger.error(f"[WhatsAppService] Error fetching admins for forward: {e}")

        success_count = 0
        for phone in admin_phones:
            try:
                if await self.forward_message(message_id, phone):
                    success_count += 1
            except Exception as e:
                logger.warning(f"[WhatsAppService] Failed to forward message {message_id} to {phone}: {e}")
        return success_count

    async def get_status(self) -> Dict[str, Any]:
        return await self.client.get_status()

    async def get_qr_data(self) -> Dict[str, Any]:
        if isinstance(self.client, WhatsAppBridgeClient):
            return await self.client.get_qr_data()
        return {"status": "READY", "raw": None, "dataUrl": None}

whatsapp_service = WhatsAppService()
