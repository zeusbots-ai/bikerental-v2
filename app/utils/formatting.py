from typing import List, Dict, Any, Optional
from app.utils.time import format_ist

def format_welcome_message(bot_name: str, hostel_name: str) -> str:
    return (
        f"🛵 *Welcome to {bot_name}!* 🚲\n"
        f"_{hostel_name} Bike & Scooty Rental Service_\n\n"
        f"To get started, please answer one quick question:\n\n"
        f"*Are you a CUAP student?*\n"
        f"👉 Reply *1* for *YES*\n"
        f"👉 Reply *2* for *NO*"
    )

def format_cuap_id_request() -> str:
    return (
        "📸 *CUAP Student Verification*\n\n"
        "Please send a clear photo of your *CUAP Student ID Card*.\n\n"
        "💡 *Tips for instant approval:*\n"
        "• Ensure your Name, Roll Number, and Photo are sharp and clearly legible.\n"
        "• Send as *HD photo* or as a *Document* (📎 -> Document) for maximum clarity.\n"
        "• Make sure 'View Once' is *turned off*."
    )

def format_non_student_message() -> str:
    return (
        "🙏 *Thank you for contacting us.*\n\n"
        "Currently, our hostel bike and scooty rental service is *exclusively available for registered CUAP students* with a valid University ID card.\n\n"
        "If you believe this was an error, send *Hi* to restart verification."
    )

def format_verification_submitted(ver_id: str) -> str:
    return (
        f"✅ *Verification Request Submitted!*\n\n"
        f"📋 *Verification ID:* `{ver_id}`\n\n"
        f"Your ID has been forwarded to the hostel warden/admin team for review. "
        f"We will notify you here on WhatsApp as soon as it is approved!\n\n"
        f"⏳ _Average review time: 5-15 minutes during operating hours._"
    )

def format_verification_approved(ver_id: str) -> str:
    return (
        f"🎉 *Verification Approved!* 🎉\n\n"
        f"Your CUAP student ID (`{ver_id}`) has been successfully verified.\n"
        f"You are now authorized to rent bikes and scooties from the hostel fleet."
    )

def format_verification_rejected(ver_id: str, reason: Optional[str]) -> str:
    reason_str = f"\n*Reason:* _{reason}_" if reason else ""
    return (
        f"❌ *Verification Not Approved*\n\n"
        f"Your ID verification (`{ver_id}`) was not approved.{reason_str}\n\n"
        f"Please send *Hi* to try again with a clearer photo of your official CUAP student ID."
    )

def format_vehicle_catalog(vehicles: List[Dict[str, Any]]) -> str:
    if not vehicles:
        return (
            "🛵 *Available Fleet*\n\n"
            "⚠️ No vehicles are currently available for booking.\n"
            "Please check back in a little while!"
        )

    lines = ["🛵 *Available Hostel Fleet for Rent* 🚲\n"]
    for i, v in enumerate(vehicles, 1):
        lines.append(
            f"*{i}. {v.get('name', 'Vehicle')}* ({v.get('type', 'SCOOTY')})\n"
            f"   • Reg No: `{v.get('registration_number', 'N/A')}`\n"
            f"   • Price: ₹{v.get('price_per_hour', 0):.0f}/hour | ₹{v.get('price_per_day', 0):.0f}/day\n"
            f"   • Description: _{v.get('description', 'Well maintained')}_\n"
            f"   • Vehicle ID: `{v.get('vehicle_id')}`\n"
        )
    lines.append(
        "👉 To select a vehicle, reply with its *number (1, 2...)* or *Vehicle ID*."
    )
    return "\n".join(lines)

def format_order_summary(order: Dict[str, Any], vehicle: Dict[str, Any], payment_url: Optional[str] = None) -> str:
    hold_time_ist = format_ist(order.get("hold_expires_at"))
    duration_str = (
        f"{order.get('duration_value', 1):.0f} hour(s)"
        if order.get("duration_type") == "HOURLY"
        else f"{order.get('duration_value', 1):.0f} day(s)"
    )

    msg = (
        f"📋 *Reservation Summary*\n\n"
        f"• *Order ID:* `{order.get('order_id')}`\n"
        f"• *Vehicle:* {vehicle.get('name')} ({vehicle.get('registration_number')})\n"
        f"• *Rental Date:* {order.get('rental_date')}\n"
        f"• *Duration:* {duration_str}\n"
        f"• *Total Amount:* ₹{order.get('total_amount', 0):.2f}\n\n"
        f"⏳ *Vehicle Held For You:*\n"
        f"Your vehicle is reserved until *{hold_time_ist}* (10 minutes).\n"
        f"Please complete payment before the timer expires, or the vehicle will be released automatically.\n\n"
    )

    if payment_url:
        msg += (
            f"💳 *Click here to pay:* \n{payment_url}\n\n"
            f"⚠️ _Note: Do not send payment screenshots. Confirmation is automated through the secure payment link._"
        )
    else:
        msg += "⚠️ _Payment gateway initialization pending. Please wait a moment._"

    return msg

def format_order_confirmed_customer(order: Dict[str, Any], vehicle: Dict[str, Any]) -> str:
    return (
        f"🎉 *Booking Confirmed!* 🛵\n\n"
        f"• *Order ID:* `{order.get('order_id')}`\n"
        f"• *Vehicle:* {vehicle.get('name')} (`{vehicle.get('registration_number')}`)\n"
        f"• *Start Date:* {order.get('rental_date')}\n"
        f"• *Amount Paid:* ₹{order.get('total_amount', 0):.2f}\n\n"
        f"🔑 *Pickup Instructions:*\n"
        f"Please visit the Hostel Security / Bike Parking desk with your CUAP Student ID.\n"
        f"Show this confirmation message to collect the keys and helmet.\n\n"
        f"Ride safely! 🛵"
    )

def format_order_expired_customer(order: Dict[str, Any], vehicle_name: str) -> str:
    return (
        f"⏰ *Reservation Expired*\n\n"
        f"Your reservation `{order.get('order_id')}` for *{vehicle_name}* has expired because payment was not completed within the 10-minute window.\n\n"
        f"The vehicle has been made available for other students. Send *Hi* if you would like to start a new booking."
    )

def format_admin_verification_alert(ver: Dict[str, Any], user_phone: str) -> str:
    ver_id = ver.get("verification_id")
    return (
        f"🔔 *NEW CUAP VERIFICATION REQUEST*\n\n"
        f"• *ID:* `{ver_id}`\n"
        f"• *Student Phone:* `+{user_phone}`\n"
        f"• *Submitted:* {format_ist(ver.get('created_at'))}\n\n"
        f"👉 *Quick Action:*\n"
        f"Swipe/reply to the photo with:\n"
        f"• `/approve`\n"
        f"• `/reject <reason>`\n"
        f"_(Or simply `/approve {ver_id}`)_"
    )

def format_admin_order_alert(order: Dict[str, Any], vehicle: Dict[str, Any]) -> str:
    return (
        f"🔔 *NEW BOOKING RESERVATION*\n\n"
        f"• *Order ID:* `{order.get('order_id')}`\n"
        f"• *Customer:* `+{order.get('user_phone')}`\n"
        f"• *Vehicle:* {vehicle.get('name')} (`{vehicle.get('vehicle_id')}`)\n"
        f"• *Amount:* ₹{order.get('total_amount', 0):.2f}\n"
        f"• *Status:* PENDING PAYMENT (10 min hold until {format_ist(order.get('hold_expires_at'))})"
    )

def format_admin_payment_alert(order: Dict[str, Any], vehicle: Dict[str, Any], payment_id: str) -> str:
    return (
        f"💰 *PAYMENT CONFIRMED!*\n\n"
        f"• *Order ID:* `{order.get('order_id')}`\n"
        f"• *Payment ID:* `{payment_id}`\n"
        f"• *Customer:* `+{order.get('user_phone')}`\n"
        f"• *Vehicle:* {vehicle.get('name')} (`{vehicle.get('registration_number')}`)\n"
        f"• *Amount Paid:* ₹{order.get('total_amount', 0):.2f}\n"
        f"• *Status:* CONFIRMED / READY FOR PICKUP"
    )
