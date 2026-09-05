import os
import logging
from typing import Optional, Dict, Any, Tuple
from app.database import get_database
from app.models.user import UserState, VerificationStatus
from app.models.verification import VerificationStatus as VerifyStatus
from app.models.vehicle import VehicleStatus
from app.services.whatsapp.service import whatsapp_service
from app.utils.formatting import (
    format_admin_verification_alert,
    format_verification_approved,
    format_verification_rejected,
    format_vehicle_catalog,
)
from app.utils.ids import generate_verification_id, generate_log_id
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

class VerificationService:

    @staticmethod
    async def create_verification_request(
        user_phone: str,
        media_file_path: str,
        mime_type: Optional[str] = None,
        original_message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        db = get_database()
        now = utc_now()
        ver_id = generate_verification_id()

        ver_doc = {
            "verification_id": ver_id,
            "user_phone": user_phone,
            "id_card_media_path": media_file_path,
            "mime_type": mime_type,
            "original_message_id": original_message_id,
            "status": VerifyStatus.PENDING.value,
            "reviewed_by": None,
            "review_reason": None,
            "reviewed_at": None,
            "created_at": now
        }
        await db.verifications.insert_one(ver_doc)

        # Update user profile
        await db.users.update_one(
            {"phone_number": user_phone},
            {
                "$set": {
                    "verification_status": VerificationStatus.PENDING.value,
                    "current_verification_id": ver_id,
                    "state": UserState.PENDING_ADMIN_REVIEW.value,
                    "updated_at": now
                }
            },
            upsert=True
        )

        # 1. Forward original full-resolution photo directly via WhatsApp (preserves 100% clarity)
        forwarded_directly = False
        if original_message_id:
            try:
                forward_count = await whatsapp_service.forward_to_admins(original_message_id)
                if forward_count > 0:
                    forwarded_directly = True
                    logger.info(f"[VerificationService] Directly forwarded photo {original_message_id} to {forward_count} admin(s)")
            except Exception as fe:
                logger.warning(f"[VerificationService] Direct forward failed, falling back to media file: {fe}")

        # 2. Forward alert instruction message to all admins
        alert_text = format_admin_verification_alert(ver_doc, user_phone)
        try:
            # If already forwarded directly, we don't need to re-upload the file
            valid_path = media_file_path if (not forwarded_directly and media_file_path and os.path.exists(media_file_path)) else None
            admin_count = await whatsapp_service.notify_admins(
                alert_text,
                file_path=valid_path,
                mime_type=mime_type
            )
            logger.info(f"[VerificationService] Successfully notified {admin_count} admin(s) for verification {ver_id}")
        except Exception as e:
            logger.error(f"[VerificationService] Error notifying admins with media: {e}")

        logger.info(f"[VerificationService] Created verification request {ver_id} for {user_phone}")
        return ver_doc

    @staticmethod
    async def approve_verification(admin_phone: str, verification_id: str) -> Tuple[bool, str]:
        db = get_database()
        now = utc_now()
        clean_vid = verification_id.strip()

        ver = await db.verifications.find_one({
            "$or": [
                {"verification_id": clean_vid},
                {"verification_id": clean_vid.upper()},
                {"verification_id": f"VER-{clean_vid.upper()}"},
                {"verification_id": {"$regex": f"{clean_vid}$", "$options": "i"}}
            ]
        })
        if not ver:
            return False, f"Verification ID `{verification_id}` not found."

        actual_id = ver.get("verification_id", clean_vid)

        if ver.get("status") == VerifyStatus.APPROVED.value:
            return False, f"Verification `{actual_id}` is already approved."

        # Atomic update verification
        await db.verifications.update_one(
            {"_id": ver["_id"]},
            {
                "$set": {
                    "status": VerifyStatus.APPROVED.value,
                    "reviewed_by": admin_phone,
                    "reviewed_at": now
                }
            }
        )

        user_phone = ver.get("user_phone")

        # Update user document
        await db.users.update_one(
            {"phone_number": user_phone},
            {
                "$set": {
                    "is_cuap_student": True,
                    "verification_status": VerificationStatus.APPROVED.value,
                    "state": UserState.IDLE.value,
                    "updated_at": now
                }
            }
        )

        # Audit log
        await db.audit_logs.insert_one({
            "log_id": generate_log_id(),
            "admin_phone": admin_phone,
            "action": "APPROVE_VERIFICATION",
            "target_id": actual_id,
            "details": {"user_phone": user_phone},
            "timestamp": now
        })

        # Fetch available vehicles to show to student
        available_vehicles = await db.vehicles.find(
            {"availability_status": VehicleStatus.AVAILABLE.value}
        ).to_list(length=50)

        catalog_text = format_vehicle_catalog(available_vehicles)
        approval_msg = f"{format_verification_approved(actual_id)}\n\n{catalog_text}"

        await whatsapp_service.send_message(user_phone, approval_msg)
        return True, f"✅ Verification `{actual_id}` for `+{user_phone}` APPROVED. Customer has been notified with the vehicle catalog."

    @staticmethod
    async def reject_verification(admin_phone: str, verification_id: str, reason: Optional[str] = None) -> Tuple[bool, str]:
        db = get_database()
        now = utc_now()
        clean_vid = verification_id.strip()

        ver = await db.verifications.find_one({
            "$or": [
                {"verification_id": clean_vid},
                {"verification_id": clean_vid.upper()},
                {"verification_id": f"VER-{clean_vid.upper()}"},
                {"verification_id": {"$regex": f"{clean_vid}$", "$options": "i"}}
            ]
        })
        if not ver:
            return False, f"Verification ID `{verification_id}` not found."

        actual_id = ver.get("verification_id", clean_vid)

        await db.verifications.update_one(
            {"_id": ver["_id"]},
            {
                "$set": {
                    "status": VerifyStatus.REJECTED.value,
                    "reviewed_by": admin_phone,
                    "review_reason": reason,
                    "reviewed_at": now
                }
            }
        )

        user_phone = ver.get("user_phone")

        # Reset user so they can try again
        await db.users.update_one(
            {"phone_number": user_phone},
            {
                "$set": {
                    "verification_status": VerificationStatus.REJECTED.value,
                    "state": UserState.IDLE.value,
                    "updated_at": now
                }
            }
        )

        # Audit log
        await db.audit_logs.insert_one({
            "log_id": generate_log_id(),
            "admin_phone": admin_phone,
            "action": "REJECT_VERIFICATION",
            "target_id": verification_id,
            "details": {"user_phone": user_phone, "reason": reason},
            "timestamp": now
        })

        rejection_msg = format_verification_rejected(verification_id, reason)
        await whatsapp_service.send_message(user_phone, rejection_msg)

        return True, f"🚫 Verification `{verification_id}` for `+{user_phone}` REJECTED. Student notified."
