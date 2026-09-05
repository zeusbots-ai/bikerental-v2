import logging
from datetime import timedelta
from typing import Tuple, Optional, Dict, Any
from app.config import settings
from app.database import get_database
from app.models.order import OrderModel, OrderStatus, DurationType
from app.models.payment import PaymentModel, PaymentStatus
from app.models.vehicle import VehicleStatus
from app.services.payments.service import get_payment_gateway
from app.services.whatsapp.service import whatsapp_service
from app.utils.formatting import format_admin_order_alert
from app.utils.ids import generate_order_id, generate_payment_id
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

def calculate_rental_price(vehicle: Dict[str, Any], duration_type: DurationType, duration_value: float) -> float:
    if duration_type == DurationType.HOURLY:
        hourly_rate = float(vehicle.get("price_per_hour", 50.0))
        return round(hourly_rate * duration_value, 2)
    else:
        daily_rate = float(vehicle.get("price_per_day", 350.0))
        return round(daily_rate * duration_value, 2)

async def hold_vehicle_and_create_order(
    user_phone: str,
    vehicle_id: str,
    rental_date: str,
    duration_type: DurationType,
    duration_value: float
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Atomically holds a vehicle for 10 minutes and generates the order & payment link.
    Returns: (order_dict, payment_url, error_message)
    """
    db = get_database()
    if db is None:
        return None, None, "Database service is not ready."

    # 1. Check if new bookings are enabled by admin (/start vs /end)
    sys_settings = await db.settings.find_one({"key": "system_config"})
    if sys_settings and not sys_settings.get("is_booking_enabled", True):
        return None, None, "The hostel bike rental service is temporarily not accepting new bookings. Please contact the warden/admin."

    now = utc_now()
    hold_expires_at = now + timedelta(minutes=settings.HOLD_EXPIRY_MINUTES)

    # 2. Concurrency-Safe Atomic Vehicle Hold
    # find_one_and_update guarantees that only one concurrent customer succeeds in holding the vehicle
    vehicle = await db.vehicles.find_one_and_update(
        {
            "vehicle_id": vehicle_id,
            "availability_status": VehicleStatus.AVAILABLE.value
        },
        {
            "$set": {
                "availability_status": VehicleStatus.HELD.value,
                "updated_at": now
            }
        },
        return_document=True
    )

    if not vehicle:
        return None, None, f"Vehicle '{vehicle_id}' is no longer available. It may have just been reserved by another student."

    # 3. Calculate Pricing
    total_amount = calculate_rental_price(vehicle, duration_type, duration_value)
    order_id = generate_order_id()

    # 4. Generate Payment Session via Payment Gateway
    gateway = get_payment_gateway()
    try:
        user = await db.users.find_one({"phone_number": user_phone})
        customer_name = user.get("name") if user else f"Student {user_phone[-4:]}"

        payment_session = await gateway.create_payment_link(
            order_id=order_id,
            amount=total_amount,
            customer_phone=user_phone,
            customer_name=customer_name,
            description=f"Rental of {vehicle.get('name')}",
            expires_in_seconds=settings.HOLD_EXPIRY_MINUTES * 60
        )
        payment_id = payment_session.get("payment_id")
        payment_url = payment_session.get("payment_url")
    except Exception as e:
        logger.error(f"[BookingEngine] Failed to create payment session: {e}")
        # Release held vehicle back to available
        await db.vehicles.update_one(
            {"vehicle_id": vehicle_id},
            {"$set": {"availability_status": VehicleStatus.AVAILABLE.value, "updated_at": utc_now()}}
        )
        return None, None, "Could not initialize payment gateway. Please try again in a few moments."

    # 5. Persist Order in MongoDB
    order_doc = {
        "order_id": order_id,
        "user_phone": user_phone,
        "vehicle_id": vehicle_id,
        "rental_date": rental_date,
        "duration_type": duration_type.value,
        "duration_value": duration_value,
        "total_amount": total_amount,
        "status": OrderStatus.PENDING_PAYMENT.value,
        "hold_expires_at": hold_expires_at,
        "payment_id": payment_id,
        "created_at": now,
        "updated_at": now
    }
    await db.orders.insert_one(order_doc)

    # 6. Persist Payment Record in MongoDB
    payment_doc = {
        "payment_id": payment_id,
        "order_id": order_id,
        "amount": total_amount,
        "currency": "INR",
        "status": PaymentStatus.PENDING.value,
        "provider": settings.PAYMENT_PROVIDER,
        "provider_payment_id": None,
        "created_at": now,
        "expires_at": hold_expires_at,
        "verified_at": None
    }
    await db.payments.insert_one(payment_doc)

    # 7. Notify Admins about the New Hold
    admin_alert = format_admin_order_alert(order_doc, vehicle)
    await whatsapp_service.notify_admins(admin_alert)

    logger.info(f"[BookingEngine] Created hold for order {order_id}, vehicle {vehicle_id}, expires at {hold_expires_at.isoformat()}")
    return order_doc, payment_url, None
