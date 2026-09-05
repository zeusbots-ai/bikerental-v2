import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from app.config import settings
from app.database import get_database
from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.models.vehicle import VehicleStatus
from app.models.user import UserState
from app.services.payments.base import BasePaymentGateway
from app.services.payments.mock import MockPaymentGateway
from app.services.payments.razorpay import RazorpayPaymentGateway
from app.services.whatsapp.service import whatsapp_service
from app.utils.formatting import format_order_confirmed_customer, format_admin_payment_alert
from app.utils.ids import generate_log_id
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

def get_payment_gateway() -> BasePaymentGateway:
    if settings.PAYMENT_PROVIDER == "razorpay":
        return RazorpayPaymentGateway()
    return MockPaymentGateway()

async def process_verified_payment(
    order_id: str,
    payment_id: str,
    provider_payment_id: str,
    idempotency_key: str,
    amount: float
) -> Dict[str, Any]:
    """
    Idempotently handles a verified payment callback.
    Guarantees no double confirmations or race conditions.
    """
    db = get_database()
    if db is None:
        raise RuntimeError("Database not initialized")

    # 1. Check existing payment record
    payment = await db.payments.find_one({"order_id": order_id})
    if not payment:
        logger.error(f"[PaymentService] Payment record not found for order {order_id}")
        return {"status": "ERROR", "message": "Payment record not found"}

    if payment.get("status") == PaymentStatus.VERIFIED.value:
        logger.info(f"[PaymentService] Payment for order {order_id} was already verified. Skipping duplicate callback.")
        return {"status": "ALREADY_PROCESSED", "order_id": order_id}

    # 2. Check if idempotency key was already recorded
    existing_key = await db.payments.find_one({"idempotency_key": idempotency_key})
    if existing_key and existing_key.get("order_id") != order_id:
        logger.error(f"[PaymentService] Idempotency key {idempotency_key} collided with another order.")
        return {"status": "ERROR", "message": "Idempotency key conflict"}

    now = utc_now()

    # 3. Atomic update to payment record
    update_res = await db.payments.find_one_and_update(
        {
            "order_id": order_id,
            "status": {"$in": [PaymentStatus.PENDING.value, "PENDING"]}
        },
        {
            "$set": {
                "status": PaymentStatus.VERIFIED.value,
                "provider_payment_id": provider_payment_id,
                "idempotency_key": idempotency_key,
                "verified_at": now
            }
        },
        return_document=True
    )

    if not update_res:
        logger.warning(f"[PaymentService] Payment {order_id} could not be atomically transitioned to VERIFIED.")
        return {"status": "ALREADY_PROCESSED", "order_id": order_id}

    # 4. Atomic update to order record
    order = await db.orders.find_one_and_update(
        {"order_id": order_id},
        {
            "$set": {
                "status": OrderStatus.CONFIRMED.value,
                "payment_id": payment_id,
                "updated_at": now
            }
        },
        return_document=True
    )

    if not order:
        logger.error(f"[PaymentService] Order {order_id} not found during confirmation.")
        return {"status": "ERROR", "message": "Order not found"}

    vehicle_id = order.get("vehicle_id")
    user_phone = order.get("user_phone")

    # 5. Atomic update to vehicle status from HELD to BOOKED
    vehicle = await db.vehicles.find_one_and_update(
        {"vehicle_id": vehicle_id},
        {
            "$set": {
                "availability_status": VehicleStatus.BOOKED.value,
                "updated_at": now
            }
        },
        return_document=True
    )

    # 6. Reset user conversation state back to IDLE
    await db.users.update_one(
        {"phone_number": user_phone},
        {
            "$set": {
                "state": UserState.IDLE.value,
                "temp_booking": {},
                "updated_at": now
            }
        }
    )

    # 7. Send confirmation to customer on WhatsApp
    customer_msg = format_order_confirmed_customer(order, vehicle or {"name": "Vehicle", "registration_number": "N/A"})
    await whatsapp_service.send_message(user_phone, customer_msg)

    # 8. Send alert to all admins on WhatsApp
    admin_msg = format_admin_payment_alert(order, vehicle or {"name": "Vehicle", "registration_number": "N/A"}, payment_id)
    await whatsapp_service.notify_admins(admin_msg)

    # 9. Audit log entry
    await db.audit_logs.insert_one({
        "log_id": generate_log_id(),
        "admin_phone": "SYSTEM_PAYMENT_GATEWAY",
        "action": "PAYMENT_CONFIRMED",
        "target_id": order_id,
        "details": {
            "amount": amount,
            "payment_id": payment_id,
            "provider_payment_id": provider_payment_id,
            "vehicle_id": vehicle_id,
            "user_phone": user_phone
        },
        "timestamp": now
    })

    logger.info(f"[PaymentService] Successfully processed payment for order {order_id}. Vehicle {vehicle_id} marked BOOKED.")
    return {"status": "SUCCESS", "order_id": order_id}
