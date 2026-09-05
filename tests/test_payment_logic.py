import hmac
import hashlib
import json
import unittest
from app.services.payments.mock import MockPaymentGateway
from app.services.payments.razorpay import RazorpayPaymentGateway

class TestPaymentLogic(unittest.IsolatedAsyncioTestCase):

    async def test_mock_gateway_link_creation(self):
        gateway = MockPaymentGateway()
        res = await gateway.create_payment_link(
            order_id="ORD-20260902-1234",
            amount=250.0,
            customer_phone="919876543210"
        )
        self.assertIn("payment_id", res)
        self.assertIn("payment_url", res)
        self.assertTrue(res["payment_url"].startswith("http"))
        self.assertIn("ORD-20260902-1234", res["payment_url"])

    def test_mock_gateway_webhook_parsing(self):
        gateway = MockPaymentGateway()
        payload = {
            "order_id": "ORD-20260902-1234",
            "payment_id": "PAY-20260902-5678",
            "amount": 250.0,
            "status": "success"
        }
        parsed = gateway.parse_webhook_payload(payload)
        self.assertEqual(parsed["order_id"], "ORD-20260902-1234")
        self.assertEqual(parsed["status"], "VERIFIED")
        self.assertEqual(parsed["amount"], 250.0)

    def test_razorpay_signature_verification(self):
        secret = "super_secret_webhook_key"
        gateway = RazorpayPaymentGateway(webhook_secret=secret)

        body_bytes = json.dumps({"event": "payment_link.paid"}).encode("utf-8")
        valid_signature = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

        # Valid signature header
        headers = {"X-Razorpay-Signature": valid_signature}
        self.assertTrue(gateway.verify_webhook_signature(headers, body_bytes))

        # Invalid signature header
        bad_headers = {"X-Razorpay-Signature": "invalid_signature_hash"}
        self.assertFalse(gateway.verify_webhook_signature(bad_headers, body_bytes))

if __name__ == "__main__":
    unittest.main()
