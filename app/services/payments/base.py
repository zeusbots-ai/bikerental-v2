from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime

class BasePaymentGateway(ABC):
    """
    Abstract interface for payment gateway integrations (Razorpay, Cashfree, Mock, etc.).
    """

    @abstractmethod
    async def create_payment_link(
        self,
        order_id: str,
        amount: float,
        customer_phone: str,
        customer_name: Optional[str] = None,
        description: str = "Hostel Vehicle Rental",
        expires_in_seconds: int = 600
    ) -> Dict[str, Any]:
        """
        Create a checkout session or payment link.
        Returns:
            {
                "payment_id": str,
                "payment_url": str,
                "expires_at": datetime
            }
        """
        pass

    @abstractmethod
    def verify_webhook_signature(self, headers: Dict[str, str], body_bytes: bytes) -> bool:
        """Verify the cryptographic webhook signature from the provider."""
        pass

    @abstractmethod
    def parse_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract normalized payment details from the provider's webhook payload.
        Returns:
            {
                "order_id": str,
                "payment_id": str,
                "amount": float,
                "status": "VERIFIED" | "FAILED",
                "provider_payment_id": str,
                "idempotency_key": str
            }
        """
        pass
