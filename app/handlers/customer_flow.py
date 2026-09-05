import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from app.config import settings
from app.database import get_database
from app.models.order import DurationType
from app.models.user import UserState, VerificationStatus
from app.models.vehicle import VehicleStatus
from app.services.booking.engine import hold_vehicle_and_create_order
from app.services.verification.service import VerificationService
from app.services.whatsapp.service import whatsapp_service
from app.utils.formatting import (
    format_welcome_message,
    format_cuap_id_request,
    format_non_student_message,
    format_verification_submitted,
    format_vehicle_catalog,
    format_order_summary,
)
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

class CustomerFlowHandler:
    _last_greeting_times: Dict[str, float] = {}

    @classmethod
    async def handle_message(
        cls,
        sender_phone: str,
        body: str,
        has_media: bool,
        media: Optional[Dict[str, Any]],
        raw_has_media: bool = False,
        message_id: Optional[str] = None
    ) -> None:
        db = get_database()
        clean_phone = "".join(filter(str.isdigit, sender_phone))
        now = utc_now()
        text = (body or "").strip()

        # Retrieve or create user profile
        user = await db.users.find_one({"phone_number": clean_phone})
        if not user:
            user = {
                "phone_number": clean_phone,
                "state": UserState.IDLE.value,
                "verification_status": VerificationStatus.UNVERIFIED.value,
                "is_cuap_student": False,
                "created_at": now,
                "updated_at": now
            }
            await db.users.insert_one(user)

        state = user.get("state", UserState.IDLE.value)
        ver_status = user.get("verification_status", VerificationStatus.UNVERIFIED.value)

        # Global restart keywords
        if text.lower() in ["hi", "hii", "hiii", "hello", "hey", "heyy", "start", "menu", "restart"]:
            await cls._handle_initial_greeting(clean_phone, user)
            return

        # Handle customer states
        if state == UserState.IDLE.value:
            await cls._handle_initial_greeting(clean_phone, user)

        elif state == UserState.AWAITING_STUDENT_CONFIRMATION.value:
            await cls._handle_student_confirmation(clean_phone, text)

        elif state == UserState.AWAITING_ID_CARD.value:
            await cls._handle_id_card_submission(
                clean_phone, has_media, media, raw_has_media=raw_has_media, message_id=message_id
            )

        elif state == UserState.PENDING_ADMIN_REVIEW.value:
            ver_id = user.get("current_verification_id", "Pending")
            await whatsapp_service.send_message(
                clean_phone,
                f"⏳ *Verification Under Review*\n\n"
                f"Your CUAP student verification (`{ver_id}`) is currently under review by the hostel admin team.\n"
                f"You will receive an automated notification as soon as it is approved!"
            )

        elif state == UserState.SELECTING_VEHICLE.value:
            await cls._handle_vehicle_selection(clean_phone, text, user)

        elif state == UserState.SELECTING_DATE.value:
            await cls._handle_date_selection(clean_phone, text, user)

        elif state == UserState.SELECTING_DURATION_TYPE.value:
            await cls._handle_duration_type_selection(clean_phone, text, user)

        elif state == UserState.SELECTING_DURATION_VALUE.value:
            await cls._handle_duration_value_selection(clean_phone, text, user)

        elif state == UserState.AWAITING_PAYMENT.value:
            await cls._handle_awaiting_payment(clean_phone, text, has_media, user)

        else:
            # Fallback
            await cls._handle_initial_greeting(clean_phone, user)

    @classmethod
    async def _handle_initial_greeting(cls, phone: str, user: Dict[str, Any]):
        import time
        now_ts = time.time()
        last_time = cls._last_greeting_times.get(phone, 0.0)
        if now_ts - last_time < 3.0:
            logger.info(f"[CustomerFlow] Suppressing duplicate greeting for {phone} (debounced: {now_ts - last_time:.2f}s since last greeting)")
            return
        cls._last_greeting_times[phone] = now_ts

        db = get_database()
        ver_status = user.get("verification_status")

        if ver_status == VerificationStatus.APPROVED.value:
            # Already verified CUAP student! Show vehicle catalog directly
            available_vehicles = await db.vehicles.find(
                {"availability_status": VehicleStatus.AVAILABLE.value}
            ).to_list(length=50)

            catalog = format_vehicle_catalog(available_vehicles)
            msg = f"👋 *Welcome back to {settings.BOT_NAME}!* 🛵\n\n{catalog}"

            await db.users.update_one(
                {"phone_number": phone},
                {"$set": {"state": UserState.SELECTING_VEHICLE.value, "temp_booking": {}, "updated_at": utc_now()}}
            )
            await whatsapp_service.send_message(phone, msg)
        else:
            # New or unverified user
            msg = format_welcome_message(settings.BOT_NAME, settings.HOSTEL_NAME)
            await db.users.update_one(
                {"phone_number": phone},
                {"$set": {"state": UserState.AWAITING_STUDENT_CONFIRMATION.value, "updated_at": utc_now()}}
            )
            await whatsapp_service.send_message(phone, msg)

    @classmethod
    async def _handle_student_confirmation(cls, phone: str, text: str):
        db = get_database()
        clean = text.strip().lower()

        if clean in ["1", "yes", "y", "true"]:
            await db.users.update_one(
                {"phone_number": phone},
                {"$set": {"state": UserState.AWAITING_ID_CARD.value, "updated_at": utc_now()}}
            )
            await whatsapp_service.send_message(phone, format_cuap_id_request())
        elif clean in ["2", "no", "n", "false"]:
            await db.users.update_one(
                {"phone_number": phone},
                {"$set": {"state": UserState.IDLE.value, "updated_at": utc_now()}}
            )
            await whatsapp_service.send_message(phone, format_non_student_message())
        else:
            await whatsapp_service.send_message(
                phone,
                "👉 Please reply with *1* for *YES* (CUAP Student) or *2* for *NO*."
            )

    @classmethod
    async def _handle_id_card_submission(
        cls,
        phone: str,
        has_media: bool,
        media: Optional[Dict[str, Any]],
        raw_has_media: bool = False,
        message_id: Optional[str] = None
    ):
        # Case 1: User sent text only (no media attachment)
        if not has_media and not raw_has_media and not media:
            await whatsapp_service.send_message(
                phone,
                "⚠️ *Photo Required:* Please send a clear photo of your CUAP Student ID card to proceed."
            )
            return

        file_path = media.get("filePath") or media.get("file_path") if isinstance(media, dict) else None

        # Case 2: Media was detected, but bridge download failed or media object is empty
        if not has_media or not media or not file_path:
            logger.warning(f"[CustomerFlow] Media download failed or missing for {phone}: has_media={has_media}, raw_has_media={raw_has_media}, media={media}")
            if raw_has_media:
                await whatsapp_service.send_message(
                    phone,
                    "⚠️ *Media Download Failed:* We detected your attachment, but were unable to download it from WhatsApp.\n\n"
                    "💡 *Tips for sending your ID card:*\n"
                    "• Make sure 'View Once' (1-time view) is *turned off*.\n"
                    "• You can also try sending it as a *Document* (📎 -> Document) or taking a clear photo.\n\n"
                    "Please try sending the photo of your CUAP Student ID card again."
                )
            else:
                await whatsapp_service.send_message(
                    phone,
                    "⚠️ *Photo Required:* Please send a clear photo of your CUAP Student ID card to proceed."
                )
            return

        # Case 3: Verify media type (document vs image)
        mimetype = (media.get("mimetype") or media.get("mimeType") or "").lower()
        is_image = mimetype.startswith("image/")
        is_pdf = mimetype == "application/pdf" or str(file_path).lower().endswith(".pdf")
        if not is_image and not is_pdf:
            logger.warning(f"[CustomerFlow] Invalid media type submitted by {phone}: {mimetype}")
            await whatsapp_service.send_message(
                phone,
                "⚠️ *Invalid File Type:* Please send a clear photo (JPG, PNG) or PDF document of your CUAP Student ID card.\n\n"
                "Audio, video, and other file types are not accepted for ID verification."
            )
            return

        # Case 4: Handle filesystem race condition (bridge saving file to disk)
        if not os.path.isabs(file_path):
            candidate = os.path.abspath(os.path.join(settings.MEDIA_STORAGE_PATH, os.path.basename(file_path)))
            if os.path.exists(candidate):
                file_path = candidate

        file_ready = False
        for _ in range(15):
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                file_ready = True
                break
            await asyncio.sleep(0.2)

        if not file_ready:
            logger.error(f"[CustomerFlow] Media file not found or empty at {file_path} for {phone}")
            await whatsapp_service.send_message(
                phone,
                "⚠️ *File Processing Error:* We could not access the uploaded file on the server. Please try sending your ID photo again."
            )
            return

        # Case 5: Valid media saved on disk -> create verification request & notify admins
        ver_kwargs = {
            "user_phone": phone,
            "media_file_path": file_path,
            "mime_type": mimetype
        }
        if message_id:
            ver_kwargs["original_message_id"] = message_id
        ver_doc = await VerificationService.create_verification_request(**ver_kwargs)
        ver_id = ver_doc.get("verification_id")

        ack_msg = format_verification_submitted(ver_id)
        await whatsapp_service.send_message(phone, ack_msg)

    @classmethod
    async def _handle_vehicle_selection(cls, phone: str, text: str, user: Dict[str, Any]):
        db = get_database()
        available_vehicles = await db.vehicles.find(
            {"availability_status": VehicleStatus.AVAILABLE.value}
        ).to_list(length=50)

        if not available_vehicles:
            await whatsapp_service.send_message(
                phone,
                "🛵 Currently no vehicles are available for rent. Please check back later!"
            )
            return

        chosen_vehicle = None
        clean_text = text.strip()

        # Check by list index (e.g. 1, 2)
        if clean_text.isdigit():
            idx = int(clean_text) - 1
            if 0 <= idx < len(available_vehicles):
                chosen_vehicle = available_vehicles[idx]

        # Check by vehicle_id
        if not chosen_vehicle:
            for v in available_vehicles:
                if v.get("vehicle_id", "").lower() == clean_text.lower():
                    chosen_vehicle = v
                    break

        if not chosen_vehicle:
            await whatsapp_service.send_message(
                phone,
                "⚠️ Invalid selection. Please reply with the *number (1, 2...)* or *Vehicle ID* from the catalog."
            )
            return

        temp_booking = {
            "vehicle_id": chosen_vehicle.get("vehicle_id"),
            "vehicle_name": chosen_vehicle.get("name"),
            "price_per_hour": chosen_vehicle.get("price_per_hour"),
            "price_per_day": chosen_vehicle.get("price_per_day")
        }

        await db.users.update_one(
            {"phone_number": phone},
            {
                "$set": {
                    "state": UserState.SELECTING_DATE.value,
                    "temp_booking": temp_booking,
                    "updated_at": utc_now()
                }
            }
        )

        msg = (
            f"✅ Selected: *{chosen_vehicle.get('name')}*\n\n"
            f"📅 *Rental Date:*\n"
            f"When do you plan to rent? Reply with *Today*, *Tomorrow*, or a specific date (e.g. *2026-09-03*):"
        )
        await whatsapp_service.send_message(phone, msg)

    @classmethod
    async def _handle_date_selection(cls, phone: str, text: str, user: Dict[str, Any]):
        date_str = text.strip()
        if len(date_str) < 2:
            await whatsapp_service.send_message(phone, "Please provide a valid date (e.g. *Today*, *Tomorrow*).")
            return

        temp_booking = user.get("temp_booking", {})
        temp_booking["rental_date"] = date_str

        db = get_database()
        await db.users.update_one(
            {"phone_number": phone},
            {
                "$set": {
                    "state": UserState.SELECTING_DURATION_TYPE.value,
                    "temp_booking": temp_booking,
                    "updated_at": utc_now()
                }
            }
        )

        msg = (
            f"📅 Rental Date: *{date_str}*\n\n"
            f"⏱️ *Select Duration Type:*\n"
            f"👉 Reply *1* for *Hourly Rental*\n"
            f"👉 Reply *2* for *Daily Rental*"
        )
        await whatsapp_service.send_message(phone, msg)

    @classmethod
    async def _handle_duration_type_selection(cls, phone: str, text: str, user: Dict[str, Any]):
        clean = text.strip().lower()
        temp_booking = user.get("temp_booking", {})

        if clean in ["1", "hourly", "hour", "hours"]:
            duration_type = DurationType.HOURLY.value
            prompt = "⏱️ How many *hours* do you need the vehicle for? (e.g. *2*, *4*, *6*)"
        elif clean in ["2", "daily", "day", "days"]:
            duration_type = DurationType.DAILY.value
            prompt = "📅 How many *days* do you need the vehicle for? (e.g. *1*, *2*, *3*)"
        else:
            await whatsapp_service.send_message(
                phone,
                "👉 Please reply with *1* for *Hourly* or *2* for *Daily* rental."
            )
            return

        temp_booking["duration_type"] = duration_type
        db = get_database()
        await db.users.update_one(
            {"phone_number": phone},
            {
                "$set": {
                    "state": UserState.SELECTING_DURATION_VALUE.value,
                    "temp_booking": temp_booking,
                    "updated_at": utc_now()
                }
            }
        )
        await whatsapp_service.send_message(phone, prompt)

    @classmethod
    async def _handle_duration_value_selection(cls, phone: str, text: str, user: Dict[str, Any]):
        clean = text.strip()
        try:
            val = float(clean)
            if val <= 0 or val > 30:
                raise ValueError()
        except ValueError:
            await whatsapp_service.send_message(
                phone,
                "⚠️ Please reply with a valid number (e.g. *2*, *4*)."
            )
            return

        temp_booking = user.get("temp_booking", {})
        vehicle_id = temp_booking.get("vehicle_id")
        rental_date = temp_booking.get("rental_date", "Today")
        duration_type_str = temp_booking.get("duration_type", DurationType.HOURLY.value)
        duration_type = DurationType(duration_type_str)

        # Create Hold and Order
        order_doc, payment_url, err = await hold_vehicle_and_create_order(
            user_phone=phone,
            vehicle_id=vehicle_id,
            rental_date=rental_date,
            duration_type=duration_type,
            duration_value=val
        )

        if err:
            db = get_database()
            await db.users.update_one(
                {"phone_number": phone},
                {"$set": {"state": UserState.IDLE.value, "temp_booking": {}, "updated_at": utc_now()}}
            )
            await whatsapp_service.send_message(phone, f"❌ {err}\n\nSend *Hi* to see available vehicles.")
            return

        # Fetch vehicle details for summary
        db = get_database()
        vehicle = await db.vehicles.find_one({"vehicle_id": vehicle_id})
        summary_msg = format_order_summary(order_doc, vehicle or {}, payment_url)

        await db.users.update_one(
            {"phone_number": phone},
            {"$set": {"state": UserState.AWAITING_PAYMENT.value, "updated_at": utc_now()}}
        )
        await whatsapp_service.send_message(phone, summary_msg)

    @classmethod
    async def _handle_awaiting_payment(cls, phone: str, text: str, has_media: bool, user: Dict[str, Any]):
        clean = text.strip().lower()

        # Reject screenshots
        if has_media:
            await whatsapp_service.send_message(
                phone,
                "⚠️ *Payment Screenshots Not Accepted*\n\n"
                "Please do not send screenshots. All payments must be completed through the official payment link provided.\n"
                "Once completed, your booking will be confirmed automatically."
            )
            return

        if clean in ["cancel", "cancel order"]:
            db = get_database()
            order = await db.orders.find_one({"user_phone": phone, "status": "PENDING_PAYMENT"})
            if order:
                now = utc_now()
                await db.orders.update_one({"order_id": order["order_id"]}, {"$set": {"status": "CANCELLED", "updated_at": now}})
                await db.vehicles.update_one({"vehicle_id": order["vehicle_id"]}, {"$set": {"availability_status": VehicleStatus.AVAILABLE.value, "updated_at": now}})

            await db.users.update_one(
                {"phone_number": phone},
                {"$set": {"state": UserState.IDLE.value, "temp_booking": {}, "updated_at": utc_now()}}
            )
            await whatsapp_service.send_message(phone, "Reservation cancelled. The vehicle has been released.")
            return

        await whatsapp_service.send_message(
            phone,
            "⏳ Your vehicle is held for 10 minutes awaiting payment. Please complete payment using the link sent above.\n\n"
            "Or reply *cancel* to release the vehicle."
        )
