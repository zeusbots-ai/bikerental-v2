import re
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from app.config import settings
from app.database import get_database
from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.models.vehicle import VehicleStatus, VehicleType
from app.services.verification.service import VerificationService
from app.services.whatsapp.service import whatsapp_service
from app.utils.formatting import format_vehicle_catalog
from app.utils.ids import generate_log_id
from app.utils.time import utc_now, format_ist

logger = logging.getLogger(__name__)

async def is_admin_authorized(phone_number: str, sender_jid: Optional[str] = None) -> bool:
    """Validates if a phone number or WhatsApp JID/LID is an authorized admin in DB or environment."""
    clean_phone = "".join(filter(str.isdigit, phone_number))
    if clean_phone in settings.admin_phone_list:
        return True

    clean_jid = (sender_jid or "").strip()
    jid_digits = "".join(filter(str.isdigit, clean_jid.split("@")[0]))
    if jid_digits and jid_digits in settings.admin_phone_list:
        return True

    db = get_database()
    if db is not None:
        query_conditions = [{"phone_number": clean_phone}]
        if clean_jid:
            query_conditions.append({"wa_jid": clean_jid})
        if jid_digits:
            query_conditions.append({"phone_number": jid_digits})

        admin = await db.admins.find_one({"$or": query_conditions, "is_active": True})
        if admin:
            return True

        if clean_jid:
            user = await db.users.find_one({"wa_jid": clean_jid})
            if user and user.get("phone_number") in settings.admin_phone_list:
                return True

    return False

async def log_admin_action(admin_phone: str, action: str, target_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
    """Write an immutable audit log entry for every admin action."""
    db = get_database()
    if db is not None:
        await db.audit_logs.insert_one({
            "log_id": generate_log_id(),
            "admin_phone": admin_phone,
            "action": action,
            "target_id": target_id,
            "details": details or {},
            "timestamp": utc_now()
        })

class AdminCommandHandler:

    @classmethod
    async def handle_command(
        cls,
        sender_phone: str,
        command_text: str,
        sender_jid: Optional[str] = None,
        quoted_message: Optional[Dict[str, Any]] = None
    ) -> str:
        clean_phone = "".join(filter(str.isdigit, sender_phone))
        if not await is_admin_authorized(clean_phone, sender_jid):
            logger.warning(f"[Admin] Unauthorized admin command attempt from {sender_phone}: {command_text}")
            return "⛔ *Unauthorized:* You are not registered as an authorized admin."

        parts = command_text.strip().split(maxsplit=2)
        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if command == "/start":
            return await cls._cmd_start(clean_phone)
        elif command == "/end":
            return await cls._cmd_end(clean_phone)
        elif command == "/status":
            return await cls._cmd_status()
        elif command in ["/approve", "approve"]:
            return await cls._cmd_approve(clean_phone, args, quoted_message)
        elif command in ["/reject", "reject"]:
            return await cls._cmd_reject(clean_phone, args, quoted_message)
        elif command == "/vehicles":
            return await cls._cmd_vehicles(all_statuses=True)
        elif command == "/available":
            return await cls._cmd_vehicles(all_statuses=False)
        elif command == "/addvehicle":
            return await cls._cmd_add_vehicle(clean_phone, command_text)
        elif command == "/editvehicle":
            return await cls._cmd_edit_vehicle(clean_phone, args)
        elif command == "/removevehicle":
            return await cls._cmd_remove_vehicle(clean_phone, args)
        elif command == "/orders":
            return await cls._cmd_orders()
        elif command == "/order":
            return await cls._cmd_order_detail(args, quoted_message)
        elif command == "/complete":
            return await cls._cmd_complete_order(clean_phone, args, quoted_message)
        elif command == "/cancel":
            return await cls._cmd_cancel_order(clean_phone, args, quoted_message)
        elif command == "/payment":
            return await cls._cmd_payment(args, quoted_message)
        elif command == "/stats":
            return await cls._cmd_stats()
        elif command == "/help":
            return cls._cmd_help()
        else:
            return f"❓ Unknown admin command `{command}`. Reply `/help` for available commands."

    @staticmethod
    def _cmd_help() -> str:
        return (
            "🛠️ *Admin Command Center:*\n\n"
            "• `/start` - Allow new bookings\n"
            "• `/end` - Stop accepting new bookings\n"
            "• `/status` - System health & operational status\n"
            "• `/approve <VER-ID>` - Approve student verification\n"
            "• `/reject <VER-ID> [reason]` - Reject student verification\n"
            "• `/vehicles` - View all fleet vehicles\n"
            "• `/available` - View only available fleet\n"
            "• `/addvehicle <Name> | <SCOOTY/BIKE> | <RegNo> | <₹/hr> | <₹/day> | <Desc>`\n"
            "• `/editvehicle <VEH-ID> <field> <value>`\n"
            "• `/removevehicle <VEH-ID>` - Set to MAINTENANCE\n"
            "• `/orders` - List recent orders\n"
            "• `/order <ORD-ID>` - View order details\n"
            "• `/complete <ORD-ID>` - Mark rental returned/done\n"
            "• `/cancel <ORD-ID> [reason]` - Cancel booking\n"
            "• `/payment <ORD-ID>` - Check payment status\n"
            "• `/stats` - Rental & revenue statistics"
        )

    @staticmethod
    async def _cmd_start(admin_phone: str) -> str:
        db = get_database()
        await db.settings.update_one(
            {"key": "system_config"},
            {"$set": {"is_booking_enabled": True, "updated_at": utc_now(), "updated_by": admin_phone}},
            upsert=True
        )
        await log_admin_action(admin_phone, "START_BOOKINGS")
        return "🟢 *Booking System STARTED:* Students can now reserve vehicles."

    @staticmethod
    async def _cmd_end(admin_phone: str) -> str:
        db = get_database()
        await db.settings.update_one(
            {"key": "system_config"},
            {"$set": {"is_booking_enabled": False, "updated_at": utc_now(), "updated_by": admin_phone}},
            upsert=True
        )
        await log_admin_action(admin_phone, "END_BOOKINGS")
        return "🔴 *Booking System HALTED:* New booking requests are stopped. Existing confirmed bookings continue normally."

    @staticmethod
    async def _cmd_status() -> str:
        db = get_database()
        settings_doc = await db.settings.find_one({"key": "system_config"})
        is_open = settings_doc.get("is_booking_enabled", True) if settings_doc else True

        total_vehicles = await db.vehicles.count_documents({})
        available_vehicles = await db.vehicles.count_documents({"availability_status": VehicleStatus.AVAILABLE.value})
        held_vehicles = await db.vehicles.count_documents({"availability_status": VehicleStatus.HELD.value})
        booked_vehicles = await db.vehicles.count_documents({"availability_status": {"$in": [VehicleStatus.BOOKED.value, VehicleStatus.RENTED.value]}})

        pending_verifications = await db.verifications.count_documents({"status": "PENDING"})
        pending_payments = await db.orders.count_documents({"status": OrderStatus.PENDING_PAYMENT.value})

        bridge_status = await whatsapp_service.get_status()
        wa_status = bridge_status.get("status", "UNKNOWN")

        return (
            f"📊 *Hostel Rental System Status*\n\n"
            f"• *Booking Acceptance:* {'🟢 ACTIVE' if is_open else '🔴 CLOSED'}\n"
            f"• *WhatsApp Web Bridge:* `{wa_status}`\n"
            f"• *Pending Verifications:* {pending_verifications}\n"
            f"• *Orders Awaiting Payment:* {pending_payments}\n\n"
            f"🛵 *Fleet Status:*\n"
            f"  - Available: {available_vehicles} / {total_vehicles}\n"
            f"  - Held (10-min reservation): {held_vehicles}\n"
            f"  - Currently Booked/Rented: {booked_vehicles}"
        )

    @staticmethod
    async def _cmd_approve(admin_phone: str, args: List[str], quoted_message: Optional[Dict[str, Any]] = None) -> str:
        db = get_database()
        ver_id = None

        # 1. Check if ID was explicitly provided in args: /approve VER-1234 or /approve 1234
        if args:
            ver_id = args[0].strip()

        # 2. Check if admin swiped/replied to a photo or verification alert
        if not ver_id and quoted_message:
            quoted_text = (quoted_message.get("body") or "") + " " + (quoted_message.get("caption") or "")
            match = re.search(r'\b(VER-[A-HJ-NP-Z2-9\d-]+)\b', quoted_text, re.IGNORECASE)
            if match:
                ver_id = match.group(1).upper()
            else:
                quoted_msg_id = quoted_message.get("message_id")
                if quoted_msg_id and db is not None:
                    ver_doc = await db.verifications.find_one({
                        "$or": [
                            {"admin_alert_message_id": quoted_msg_id},
                            {"original_message_id": quoted_msg_id}
                        ]
                    })
                    if ver_doc:
                        ver_id = ver_doc.get("verification_id")

        # 3. If still no ID (admin simply typed /approve or approve), pick latest pending verification!
        if not ver_id and db is not None:
            pending = await db.verifications.find_one({"status": "PENDING"}, sort=[("created_at", -1)])
            if pending:
                ver_id = pending.get("verification_id")

        if not ver_id:
            return "⚠️ No pending verification found to approve.\n\n💡 *Tip:* Swipe/reply to the student ID photo with `/approve` or type `/approve <VER-ID>`."

        success, msg = await VerificationService.approve_verification(admin_phone, ver_id)
        return msg

    @staticmethod
    async def _cmd_reject(admin_phone: str, args: List[str], quoted_message: Optional[Dict[str, Any]] = None) -> str:
        db = get_database()
        ver_id = None
        reason = None

        # 1. Check if first arg is an explicit ID
        if args:
            first = args[0].strip()
            if first.upper().startswith("VER-") or (len(first) <= 8 and db is not None and await db.verifications.find_one({"verification_id": {"$regex": f"{first}$", "$options": "i"}})):
                ver_id = first
                reason = " ".join(args[1:]) if len(args) > 1 else None
            else:
                # The entire argument list is the rejection reason!
                reason = " ".join(args)

        # 2. Check quoted message
        if not ver_id and quoted_message:
            quoted_text = (quoted_message.get("body") or "") + " " + (quoted_message.get("caption") or "")
            match = re.search(r'\b(VER-[A-HJ-NP-Z2-9\d-]+)\b', quoted_text, re.IGNORECASE)
            if match:
                ver_id = match.group(1).upper()
            else:
                quoted_msg_id = quoted_message.get("message_id")
                if quoted_msg_id and db is not None:
                    ver_doc = await db.verifications.find_one({
                        "$or": [
                            {"admin_alert_message_id": quoted_msg_id},
                            {"original_message_id": quoted_msg_id}
                        ]
                    })
                    if ver_doc:
                        ver_id = ver_doc.get("verification_id")

        # 3. If still no ID, pick latest pending verification
        if not ver_id and db is not None:
            pending = await db.verifications.find_one({"status": "PENDING"}, sort=[("created_at", -1)])
            if pending:
                ver_id = pending.get("verification_id")

        if not ver_id:
            return "⚠️ No pending verification found to reject.\n\n💡 *Tip:* Swipe/reply to the student ID photo with `/reject <reason>`."

        success, msg = await VerificationService.reject_verification(admin_phone, ver_id, reason)
        return msg

    @staticmethod
    async def _cmd_vehicles(all_statuses: bool = True) -> str:
        db = get_database()
        query = {} if all_statuses else {"availability_status": VehicleStatus.AVAILABLE.value}
        vehicles = await db.vehicles.find(query).to_list(length=100)

        if not vehicles:
            return "ℹ️ No vehicles found in database."

        lines = ["🛵 *Fleet Overview:*\n"]
        for v in vehicles:
            status_icon = {
                VehicleStatus.AVAILABLE.value: "🟢",
                VehicleStatus.HELD.value: "⏳",
                VehicleStatus.BOOKED.value: "🔒",
                VehicleStatus.RENTED.value: "🚀",
                VehicleStatus.MAINTENANCE.value: "🔧"
            }.get(v.get("availability_status"), "⚪")

            lines.append(
                f"{status_icon} *{v.get('name')}* (`{v.get('vehicle_id')}`)\n"
                f"   • Reg: `{v.get('registration_number')}` | {v.get('type')}\n"
                f"   • Rates: ₹{v.get('price_per_hour', 0):.0f}/hr | ₹{v.get('price_per_day', 0):.0f}/day\n"
                f"   • Status: *{v.get('availability_status')}*\n"
            )
        return "\n".join(lines)

    @staticmethod
    async def _cmd_add_vehicle(admin_phone: str, full_cmd: str) -> str:
        # Example: /addvehicle Honda Activa 6G | SCOOTY | AP02AB1234 | 50 | 350 | Smooth ride
        payload = full_cmd[len("/addvehicle"):].strip()
        parts = [p.strip() for p in payload.split("|")]
        if len(parts) < 5:
            return (
                "⚠️ Usage:\n"
                "`/addvehicle <Name> | <SCOOTY/BIKE> | <RegNo> | <Price_Hour> | <Price_Day> | <Optional Description>`"
            )

        name, v_type_raw, reg_no, p_hr, p_day = parts[:5]
        desc = parts[5] if len(parts) > 5 else "Available for rental"

        try:
            p_hr_float = float(p_hr)
            p_day_float = float(p_day)
        except ValueError:
            return "❌ Prices must be valid numbers (e.g. 50, 350)."

        v_type = VehicleType.BIKE.value if "bike" in v_type_raw.lower() else VehicleType.SCOOTY.value
        db = get_database()

        # Generate vehicle ID
        count = await db.vehicles.count_documents({})
        prefix = "ACTIVA" if "activa" in name.lower() else ("BIKE" if v_type == VehicleType.BIKE.value else "SCOOT")
        vehicle_id = f"{prefix}-{count + 1:02d}"

        now = utc_now()
        vehicle_doc = {
            "vehicle_id": vehicle_id,
            "name": name,
            "type": v_type,
            "registration_number": reg_no.upper().replace(" ", ""),
            "description": desc,
            "photos": [],
            "price_per_hour": p_hr_float,
            "price_per_day": p_day_float,
            "availability_status": VehicleStatus.AVAILABLE.value,
            "created_at": now,
            "updated_at": now
        }
        await db.vehicles.insert_one(vehicle_doc)
        await log_admin_action(admin_phone, "ADD_VEHICLE", vehicle_id, vehicle_doc)

        return (
            f"✅ *Vehicle Added Successfully!*\n\n"
            f"• *ID:* `{vehicle_id}`\n"
            f"• *Name:* {name}\n"
            f"• *Registration:* `{reg_no.upper()}`\n"
            f"• *Price:* ₹{p_hr_float:.0f}/hr | ₹{p_day_float:.0f}/day"
        )

    @staticmethod
    async def _cmd_edit_vehicle(admin_phone: str, args: List[str]) -> str:
        if len(args) < 3:
            return "⚠️ Usage: `/editvehicle <VEH-ID> <field> <value>`\nFields: `price_hr`, `price_day`, `status`, `name`"
        veh_id, field, val = args[0].strip(), args[1].lower(), args[2].strip()

        db = get_database()
        vehicle = await db.vehicles.find_one({"vehicle_id": veh_id})
        if not vehicle:
            return f"❌ Vehicle `{veh_id}` not found."

        update_field = {}
        if field in ["price_hr", "price_hour", "price_per_hour"]:
            update_field["price_per_hour"] = float(val)
        elif field in ["price_day", "price_per_day"]:
            update_field["price_per_day"] = float(val)
        elif field == "status":
            clean_status = val.upper()
            if clean_status not in [s.value for s in VehicleStatus]:
                return f"❌ Invalid status. Options: {[s.value for s in VehicleStatus]}"
            update_field["availability_status"] = clean_status
        elif field == "name":
            update_field["name"] = val
        else:
            return "❌ Unsupported field. Allowed: `price_hr`, `price_day`, `status`, `name`."

        update_field["updated_at"] = utc_now()
        await db.vehicles.update_one({"vehicle_id": veh_id}, {"$set": update_field})
        await log_admin_action(admin_phone, "EDIT_VEHICLE", veh_id, update_field)

        return f"✅ Vehicle `{veh_id}` updated: `{field}` = `{val}`."

    @staticmethod
    async def _cmd_remove_vehicle(admin_phone: str, args: List[str]) -> str:
        if not args:
            return "⚠️ Usage: `/removevehicle <VEH-ID>`"
        veh_id = args[0].strip()
        db = get_database()
        res = await db.vehicles.update_one(
            {"vehicle_id": veh_id},
            {"$set": {"availability_status": VehicleStatus.MAINTENANCE.value, "updated_at": utc_now()}}
        )
        if res.matched_count == 0:
            return f"❌ Vehicle `{veh_id}` not found."

        await log_admin_action(admin_phone, "REMOVE_VEHICLE_TO_MAINTENANCE", veh_id)
        return f"🔧 Vehicle `{veh_id}` marked as `MAINTENANCE` and removed from active booking catalog."

    @staticmethod
    async def _cmd_orders() -> str:
        db = get_database()
        orders = await db.orders.find().sort("created_at", -1).to_list(length=10)
        if not orders:
            return "ℹ️ No orders recorded yet."

        lines = ["📋 *Recent 10 Orders:*\n"]
        for o in orders:
            status_icon = "🟢" if o.get("status") == OrderStatus.CONFIRMED.value else ("⏳" if o.get("status") == OrderStatus.PENDING_PAYMENT.value else "⚪")
            lines.append(
                f"{status_icon} `{o.get('order_id')}` | +{o.get('user_phone')}\n"
                f"   • Veh: `{o.get('vehicle_id')}` | ₹{o.get('total_amount', 0):.0f} | *{o.get('status')}*\n"
                f"   • Created: {format_ist(o.get('created_at'))}\n"
            )
        lines.append("👉 Reply `/order <ORD-ID>` for complete details.")
        return "\n".join(lines)

    @staticmethod
    async def _find_order(db, ord_code: str):
        clean = ord_code.strip()
        return await db.orders.find_one({
            "$or": [
                {"order_id": clean},
                {"order_id": clean.upper()},
                {"order_id": f"ORD-{clean.upper()}"},
                {"order_id": {"$regex": f"{clean}$", "$options": "i"}}
            ]
        })

    @staticmethod
    def _extract_order_id(args: List[str], quoted_message: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if args:
            return args[0].strip()
        if quoted_message:
            quoted_text = (quoted_message.get("body") or "") + " " + (quoted_message.get("caption") or "")
            match = re.search(r'\b(ORD-[A-HJ-NP-Z2-9\d-]+)\b', quoted_text, re.IGNORECASE)
            if match:
                return match.group(1).upper()
        return None

    @classmethod
    async def _cmd_order_detail(cls, args: List[str], quoted_message: Optional[Dict[str, Any]] = None) -> str:
        ord_id = cls._extract_order_id(args, quoted_message)
        if not ord_id:
            return "⚠️ Usage: `/order <ORD-ID>` (or swipe/reply to an order notification)"
        db = get_database()
        order = await cls._find_order(db, ord_id)
        if not order:
            return f"❌ Order `{ord_id}` not found."

        actual_id = order.get("order_id", ord_id)
        vehicle = await db.vehicles.find_one({"vehicle_id": order.get("vehicle_id")})
        payment = await db.payments.find_one({"order_id": actual_id})

        return (
            f"📋 *Order Details: {actual_id}*\n\n"
            f"• *Customer:* `+{order.get('user_phone')}`\n"
            f"• *Vehicle:* {vehicle.get('name') if vehicle else 'N/A'} (`{order.get('vehicle_id')}`)\n"
            f"• *Date:* {order.get('rental_date')}\n"
            f"• *Duration:* {order.get('duration_value')} {order.get('duration_type')}\n"
            f"• *Amount:* ₹{order.get('total_amount', 0):.2f}\n"
            f"• *Status:* *{order.get('status')}*\n"
            f"• *Hold Expiry:* {format_ist(order.get('hold_expires_at'))}\n"
            f"• *Payment ID:* `{order.get('payment_id') or 'N/A'}`\n"
            f"• *Payment Status:* `{payment.get('status') if payment else 'N/A'}`\n"
            f"• *Created:* {format_ist(order.get('created_at'))}"
        )

    @classmethod
    async def _cmd_complete_order(cls, admin_phone: str, args: List[str], quoted_message: Optional[Dict[str, Any]] = None) -> str:
        ord_id = cls._extract_order_id(args, quoted_message)
        if not ord_id:
            return "⚠️ Usage: `/complete <ORD-ID>` (or swipe/reply to an order notification)"
        db = get_database()

        order = await cls._find_order(db, ord_id)
        if not order:
            return f"❌ Order `{ord_id}` not found."

        actual_id = order.get("order_id", ord_id)
        now = utc_now()
        # Mark order completed
        await db.orders.update_one(
            {"_id": order["_id"]},
            {"$set": {"status": OrderStatus.COMPLETED.value, "updated_at": now}}
        )

        # Release vehicle back to AVAILABLE
        await db.vehicles.update_one(
            {"vehicle_id": order.get("vehicle_id")},
            {"$set": {"availability_status": VehicleStatus.AVAILABLE.value, "updated_at": now}}
        )

        await log_admin_action(admin_phone, "COMPLETE_ORDER", actual_id, {"vehicle_id": order.get("vehicle_id")})

        # Notify customer
        customer_msg = (
            f"✅ *Rental Completed!*\n\n"
            f"Your rental for order `{actual_id}` has been marked as returned and completed.\n"
            f"Thank you for choosing CUAP Wheels! We hope to see you again soon."
        )
        await whatsapp_service.send_message(order.get("user_phone"), customer_msg)

        return f"🎉 Order `{actual_id}` marked as COMPLETED. Vehicle `{order.get('vehicle_id')}` is now AVAILABLE for rent."

    @classmethod
    async def _cmd_cancel_order(cls, admin_phone: str, args: List[str], quoted_message: Optional[Dict[str, Any]] = None) -> str:
        ord_id = None
        reason = "Cancelled by hostel admin"

        if args:
            first = args[0].strip()
            # If first arg looks like an order id
            if first.upper().startswith("ORD-") or len(first) <= 8:
                ord_id = first
                if len(args) > 1:
                    reason = " ".join(args[1:])
            else:
                reason = " ".join(args)

        if not ord_id and quoted_message:
            ord_id = cls._extract_order_id([], quoted_message)

        if not ord_id:
            return "⚠️ Usage: `/cancel <ORD-ID> [optional reason]` (or swipe/reply to an order notification with `/cancel`)"

        db = get_database()
        order = await cls._find_order(db, ord_id)
        if not order:
            return f"❌ Order `{ord_id}` not found."

        actual_id = order.get("order_id", ord_id)
        now = utc_now()
        await db.orders.update_one(
            {"_id": order["_id"]},
            {"$set": {"status": OrderStatus.CANCELLED.value, "updated_at": now, "notes": reason}}
        )

        # Release vehicle back to AVAILABLE
        await db.vehicles.update_one(
            {"vehicle_id": order.get("vehicle_id")},
            {"$set": {"availability_status": VehicleStatus.AVAILABLE.value, "updated_at": now}}
        )

        await log_admin_action(admin_phone, "CANCEL_ORDER", actual_id, {"reason": reason})

        # Notify customer
        customer_msg = (
            f"⚠️ *Booking Cancelled*\n\n"
            f"Your order `{actual_id}` has been cancelled.\n"
            f"*Reason:* _{reason}_\n\n"
            f"If you made a payment, please contact the hostel administration desk for assistance."
        )
        await whatsapp_service.send_message(order.get("user_phone"), customer_msg)

        return f"🚫 Order `{actual_id}` CANCELLED. Vehicle `{order.get('vehicle_id')}` released back to AVAILABLE."

    @classmethod
    async def _cmd_payment(cls, args: List[str], quoted_message: Optional[Dict[str, Any]] = None) -> str:
        ord_id = cls._extract_order_id(args, quoted_message)
        if not ord_id:
            return "⚠️ Usage: `/payment <ORD-ID>` (or swipe/reply to an order notification)"
        db = get_database()

        order = await cls._find_order(db, ord_id)
        actual_id = order.get("order_id", ord_id) if order else ord_id

        payment = await db.payments.find_one({"$or": [{"order_id": actual_id}, {"order_id": ord_id}]})
        if not payment:
            return f"❌ No payment record found for order `{actual_id}`."

        return (
            f"💳 *Payment Status for {actual_id}:*\n\n"
            f"• *Payment ID:* `{payment.get('payment_id')}`\n"
            f"• *Amount:* ₹{payment.get('amount', 0):.2f}\n"
            f"• *Status:* *{payment.get('status')}*\n"
            f"• *Provider:* `{payment.get('provider')}`\n"
            f"• *Provider Payment ID:* `{payment.get('provider_payment_id') or 'N/A'}`\n"
            f"• *Verified At:* {format_ist(payment.get('verified_at'))}"
        )

    @staticmethod
    async def _cmd_stats() -> str:
        db = get_database()
        total_orders = await db.orders.count_documents({})
        confirmed_orders = await db.orders.count_documents({"status": OrderStatus.CONFIRMED.value})
        completed_orders = await db.orders.count_documents({"status": OrderStatus.COMPLETED.value})
        verified_students = await db.users.count_documents({"verification_status": "APPROVED"})

        # Calculate total revenue from confirmed & completed payments
        pipeline = [
            {"$match": {"status": PaymentStatus.VERIFIED.value}},
            {"$group": {"_id": None, "total_revenue": {"$sum": "$amount"}}}
        ]
        revenue_cursor = db.payments.aggregate(pipeline)
        revenue_res = await revenue_cursor.to_list(length=1)
        total_rev = revenue_res[0].get("total_revenue", 0.0) if revenue_res else 0.0

        return (
            f"📈 *CUAP Wheels Rental Stats*\n\n"
            f"• *Total Bookings:* {total_orders}\n"
            f"• *Confirmed Active:* {confirmed_orders}\n"
            f"• *Completed Trips:* {completed_orders}\n"
            f"• *Verified CUAP Students:* {verified_students}\n"
            f"• *Total Revenue:* ₹{total_rev:,.2f}"
        )
