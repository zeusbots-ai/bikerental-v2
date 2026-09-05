import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from app.config import settings
from app.services.payments.base import BasePaymentGateway
from app.utils.ids import generate_payment_id

logger = logging.getLogger(__name__)

class MockPaymentGateway(BasePaymentGateway):
    """
    Mock payment gateway for local development and integration testing.
    Provides a functional web checkout screen to simulate UPI / Card success callbacks.
    """

    async def create_payment_link(
        self,
        order_id: str,
        amount: float,
        customer_phone: str,
        customer_name: Optional[str] = None,
        description: str = "Hostel Vehicle Rental",
        expires_in_seconds: int = 600
    ) -> Dict[str, Any]:
        payment_id = generate_payment_id()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)

        # Build mock interactive checkout URL
        base_host = f"http://localhost:{settings.PORT}" if "localhost" in settings.BRIDGE_URL else f"http://{settings.HOST}:{settings.PORT}"
        payment_url = f"{base_host}/api/v1/payments/mock-checkout?order_id={order_id}&payment_id={payment_id}&amount={amount:.2f}"

        return {
            "payment_id": payment_id,
            "payment_url": payment_url,
            "expires_at": expires_at
        }

    def verify_webhook_signature(self, headers: Dict[str, str], body_bytes: bytes) -> bool:
        # Mock gateway accepts all simulation requests with signature header or token
        return True

    def parse_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        order_id = payload.get("order_id")
        payment_id = payload.get("payment_id")
        amount = float(payload.get("amount", 0))
        provider_payment_id = payload.get("provider_payment_id", f"mock_pay_{payment_id}")
        idempotency_key = f"mock_{order_id}_{payment_id}"

        return {
            "order_id": order_id,
            "payment_id": payment_id,
            "amount": amount,
            "status": "VERIFIED" if payload.get("status", "success") == "success" else "FAILED",
            "provider_payment_id": provider_payment_id,
            "idempotency_key": idempotency_key
        }
