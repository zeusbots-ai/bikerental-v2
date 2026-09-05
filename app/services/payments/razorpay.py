import hmac
import hashlib
import logging
import httpx
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from app.config import settings
from app.services.payments.base import BasePaymentGateway

logger = logging.getLogger(__name__)

class RazorpayPaymentGateway(BasePaymentGateway):
    """
    Razorpay integration supporting UPI, NetBanking, and Cards.
    Verifies cryptographic HMAC SHA256 signatures on webhooks.
    """

    def __init__(self, key_id: str = None, key_secret: str = None, webhook_secret: str = None):
        self.key_id = key_id or settings.RAZORPAY_KEY_ID
        self.key_secret = key_secret or settings.RAZORPAY_KEY_SECRET
        self.webhook_secret = webhook_secret or settings.RAZORPAY_WEBHOOK_SECRET
        self.api_url = "https://api.razorpay.com/v1"

    async def create_payment_link(
        self,
        order_id: str,
        amount: float,
        customer_phone: str,
        customer_name: Optional[str] = None,
        description: str = "Hostel Vehicle Rental",
        expires_in_seconds: int = 600
    ) -> Dict[str, Any]:
        amount_paise = int(round(amount * 100))
        clean_phone = "".join(filter(str.isdigit, customer_phone))
        # Ensure 10-digit format for India customer phone if applicable
        if clean_phone.startswith("91") and len(clean_phone) == 12:
            contact_phone = clean_phone[2:]
        else:
            contact_phone = clean_phone

        expire_by = int((datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).timestamp())

        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "accept_partial": False,
            "description": f"{description} ({order_id})",
            "customer": {
                "name": customer_name or f"CUAP Student {contact_phone[-4:]}",
                "contact": f"+91{contact_phone}"
            },
            "notify": {
                "sms": False,
                "email": False
            },
            "reminder_enable": False,
            "notes": {
                "order_id": order_id
            },
            "expire_by": expire_by
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.api_url}/payment_links",
                auth=(self.key_id, self.key_secret),
                json=payload
            )

            if resp.status_code not in [200, 201]:
                logger.error(f"[Razorpay] Failed to create payment link: {resp.text}")
                raise RuntimeError(f"Failed to create Razorpay payment link: {resp.text}")

            data = resp.json()
            return {
                "payment_id": data.get("id"),
                "payment_url": data.get("short_url"),
                "expires_at": datetime.fromtimestamp(data.get("expire_by", expire_by), tz=timezone.utc)
            }

    def verify_webhook_signature(self, headers: Dict[str, str], body_bytes: bytes) -> bool:
        signature = headers.get("X-Razorpay-Signature") or headers.get("x-razorpay-signature")
        if not signature or not self.webhook_secret:
            logger.warning("[Razorpay] Missing signature or webhook secret")
            return False

        expected_signature = hmac.new(
            self.webhook_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected_signature, signature)

    def parse_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event = payload.get("event")
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        payment_link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})

        order_id = (
            payment_entity.get("notes", {}).get("order_id") or
            payment_link_entity.get("notes", {}).get("order_id")
        )
        payment_id = payment_link_entity.get("id") or payment_entity.get("id")
        amount = float(payment_entity.get("amount", 0)) / 100.0
        provider_payment_id = payment_entity.get("id")
        idempotency_key = f"razorpay_{provider_payment_id}"

        status = "VERIFIED" if event in ["payment_link.paid", "payment.captured"] else "FAILED"

        return {
            "order_id": order_id,
            "payment_id": payment_id,
            "amount": amount,
            "status": status,
            "provider_payment_id": provider_payment_id,
            "idempotency_key": idempotency_key
        }
