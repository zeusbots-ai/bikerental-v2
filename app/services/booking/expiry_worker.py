import asyncio
import logging
from datetime import datetime, timezone
from app.database import get_database
from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.models.vehicle import VehicleStatus
from app.models.user import UserState
from app.services.whatsapp.service import whatsapp_service
from app.utils.formatting import format_order_expired_customer
from app.utils.ids import generate_log_id
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

async def check_and_release_expired_holds():
    """
    Finds all PENDING_PAYMENT orders that have passed their 10-minute hold expiry.
    Atomically updates order to EXPIRED and releases the vehicle back to AVAILABLE.
    """
    db = get_database()
    if db is None:
        return

    now = utc_now()
    cursor = db.orders.find({
        "status": OrderStatus.PENDING_PAYMENT.value,
        "hold_expires_at": {"$lt": now}
    })

    expired_orders = await cursor.to_list(length=100)
    for order in expired_orders:
        order_id = order.get("order_id")
        vehicle_id = order.get("vehicle_id")
        user_phone = order.get("user_phone")

        logger.info(f"[ExpiryWorker] Releasing expired hold for order {order_id} (Vehicle: {vehicle_id})")

        # 1. Update order status to EXPIRED
        await db.orders.update_one(
            {"order_id": order_id, "status": OrderStatus.PENDING_PAYMENT.value},
            {"$set": {"status": OrderStatus.EXPIRED.value, "updated_at": now}}
        )

        # 2. Update payment status to EXPIRED
        await db.payments.update_one(
            {"order_id": order_id, "status": PaymentStatus.PENDING.value},
            {"$set": {"status": PaymentStatus.EXPIRED.value}}
        )

        # 3. Release vehicle back to AVAILABLE (only if still HELD)
        vehicle = await db.vehicles.find_one_and_update(
            {"vehicle_id": vehicle_id, "availability_status": VehicleStatus.HELD.value},
            {"$set": {"availability_status": VehicleStatus.AVAILABLE.value, "updated_at": now}},
            return_document=True
        )

        # 4. Reset customer state
        await db.users.update_one(
            {"phone_number": user_phone, "state": UserState.AWAITING_PAYMENT.value},
            {"$set": {"state": UserState.IDLE.value, "temp_booking": {}, "updated_at": now}}
        )

        # 5. Notify customer on WhatsApp
        vehicle_name = vehicle.get("name") if vehicle else "Vehicle"
        customer_msg = format_order_expired_customer(order, vehicle_name)
        await whatsapp_service.send_message(user_phone, customer_msg)

        # 6. Notify admins
        admin_alert = f"⚠️ *HOLD EXPIRED:* Order `{order_id}` for {vehicle_name} expired after 10 minutes without payment. Vehicle is now released."
        await whatsapp_service.notify_admins(admin_alert)

        # 7. Audit log
        await db.audit_logs.insert_one({
            "log_id": generate_log_id(),
            "admin_phone": "SYSTEM_EXPIRY_WORKER",
            "action": "HOLD_EXPIRED",
            "target_id": order_id,
            "details": {
                "vehicle_id": vehicle_id,
                "user_phone": user_phone,
                "total_amount": order.get("total_amount")
            },
            "timestamp": now
        })

async def run_hold_expiry_worker(interval_seconds: int = 30):
    """Continuous background loop monitoring vehicle hold expirations."""
    logger.info(f"[ExpiryWorker] Background hold expiry worker started (Polling every {interval_seconds}s)...")
    while True:
        try:
            await check_and_release_expired_holds()
        except Exception as e:
            logger.error(f"[ExpiryWorker] Error during hold expiry check: {e}")
        await asyncio.sleep(interval_seconds)
