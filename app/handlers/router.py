import json
import logging
from typing import Dict, Any, Optional
from app.admin.commands import AdminCommandHandler, is_admin_authorized
from app.handlers.customer_flow import CustomerFlowHandler
from app.services.whatsapp.service import whatsapp_service
from app.database import get_database

logger = logging.getLogger(__name__)

async def route_inbound_message(payload: Dict[str, Any]) -> None:
    """
    Main entry point for messages received from the WhatsApp Web Bridge.
    Routes admin commands to AdminCommandHandler and conversation messages to CustomerFlowHandler.
    """
    sender_phone = payload.get("sender_phone", "")
    sender_jid = payload.get("sender_jid") or payload.get("from") or ""
    message_id = payload.get("message_id")
    quoted_message = payload.get("quoted_message")
    body = (payload.get("body") or "").strip()

    # Extract media fields handling snake_case, camelCase, and types
    raw_has_media_val = payload.get("raw_has_media")
    has_media_val = payload.get("has_media") if "has_media" in payload else payload.get("hasMedia")
    media = payload.get("media") or payload.get("mediaInfo")

    has_media = bool(has_media_val) if not isinstance(has_media_val, str) else has_media_val.lower() in ("true", "1")
    raw_has_media = bool(raw_has_media_val) if raw_has_media_val is not None else has_media

    clean_phone = "".join(filter(str.isdigit, str(sender_phone)))
    if not clean_phone:
        logger.warning(f"[Router] Received inbound message with empty phone number: {payload}")
        return

    # Log full payload and inspect media structure when media is sent
    if has_media or raw_has_media or media is not None:
        file_path = media.get("filePath") or media.get("file_path") if isinstance(media, dict) else None
        mimetype = media.get("mimetype") or media.get("mimeType") if isinstance(media, dict) else None
        filesize = media.get("filesize") or media.get("filesize") if isinstance(media, dict) else None
        logger.info(
            f"[Router] Inbound MEDIA detected from {clean_phone}:\n"
            f"  FULL PAYLOAD: {json.dumps(payload, default=str)}\n"
            f"  has_media: {has_media} (type: {type(has_media_val).__name__}, raw_val: {has_media_val!r})\n"
            f"  raw_has_media: {raw_has_media} (type: {type(raw_has_media_val).__name__})\n"
            f"  media: {json.dumps(media, default=str) if isinstance(media, dict) else media!r} (type: {type(media).__name__})\n"
            f"  filePath: {file_path!r}\n"
            f"  mimetype: {mimetype!r}\n"
            f"  filesize: {filesize!r}"
        )
    else:
        logger.info(f"[Router] Incoming text message from {clean_phone}: '{body[:50]}' (has_media={has_media})")

    # If the phone number is not directly recognized as admin, check if sender_jid
    # matches a known admin wa_jid in db.admins or db.users
    from app.config import settings
    if clean_phone not in settings.admin_phone_list:
        db = get_database()
        if db is not None:
            try:
                admin = await db.admins.find_one(
                    {"$or": [{"wa_jid": sender_jid}, {"phone_number": clean_phone}], "is_active": True}
                )
                if admin and admin.get("phone_number"):
                    resolved_admin_phone = "".join(filter(str.isdigit, admin["phone_number"]))
                    if resolved_admin_phone:
                        clean_phone = resolved_admin_phone
                else:
                    user_doc = await db.users.find_one({"wa_jid": sender_jid})
                    if user_doc and user_doc.get("phone_number") in settings.admin_phone_list:
                        clean_phone = user_doc["phone_number"]
            except Exception as e:
                logger.warning(f"[Router] Error resolving admin phone by wa_jid: {e}")

    # Remember the exact WhatsApp JID for this phone (handles @lid contacts,
    # not just @c.us) so replies go back to the right identifier.
    if sender_jid:
        db = get_database()
        if db is not None:
            try:
                await db.users.update_one(
                    {"phone_number": clean_phone},
                    {"$set": {"wa_jid": sender_jid}},
                    upsert=True
                )
                if clean_phone in settings.admin_phone_list:
                    await db.admins.update_one(
                        {"phone_number": clean_phone},
                        {"$set": {"wa_jid": sender_jid}}
                    )
            except Exception as e:
                logger.error(f"[Router] Failed to persist wa_jid for {clean_phone}: {e}")

    # Check if message is an admin command (starts with / or is a swipe-reply like approve/reject)
    first_word = body.split()[0].lower() if body else ""
    is_swipe_reply_cmd = (
        quoted_message is not None and first_word in ["approve", "reject", "cancel", "complete"]
    )
    is_admin_cmd = body.startswith("/") or is_swipe_reply_cmd

    if is_admin_cmd:
        cmd_text = body if body.startswith("/") else f"/{body}"
        if await is_admin_authorized(clean_phone, sender_jid):
            response_text = await AdminCommandHandler.handle_command(
                clean_phone, cmd_text, sender_jid, quoted_message
            )
            await whatsapp_service.send_message(clean_phone, response_text)
            return
        else:
            logger.info(f"[Router] Non-admin {clean_phone} attempted command: {body}")
            # Fallback to customer flow or ignore unauthorized command attempts

    # Route to Customer Interactive Flow
    await CustomerFlowHandler.handle_message(
        sender_phone=clean_phone,
        body=body,
        has_media=has_media,
        media=media,
        raw_has_media=raw_has_media,
        message_id=message_id
    )
