import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from app.database import set_database
from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.models.vehicle import VehicleStatus
from app.services.payments.service import process_verified_payment

class MockCollection:
    def __init__(self, data=None):
        self.items = data or []

    async def find_one(self, filter_dict):
        for item in self.items:
            match = True
            for k, v in filter_dict.items():
                if k.startswith("$"):
                    continue
                if isinstance(v, dict):
                    continue
                if item.get(k) != v:
                    match = False
                    break
            if match:
                return item
        return None

    async def find_one_and_update(self, filter_dict, update_dict, return_document=False):
        item = await self.find_one(filter_dict)
        if not item:
            return None
        if "$set" in update_dict:
            item.update(update_dict["$set"])
        return item

    async def update_one(self, filter_dict, update_dict, upsert=False):
        item = await self.find_one(filter_dict)
        if item and "$set" in update_dict:
            item.update(update_dict["$set"])
            return type("UpdateResult", (), {"matched_count": 1, "modified_count": 1})()
        return type("UpdateResult", (), {"matched_count": 0, "modified_count": 0})()

    async def insert_one(self, doc):
        self.items.append(doc)
        return type("InsertResult", (), {"inserted_id": "mock_id"})()

class MockDB:
    def __init__(self):
        self.payments = MockCollection()
        self.orders = MockCollection()
        self.vehicles = MockCollection()
        self.users = MockCollection()
        self.audit_logs = MockCollection()

class TestPaymentIdempotency(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.mock_db = MockDB()
        set_database(self.mock_db)

        # Prepopulate records for order ORD-1
        self.mock_db.orders.items.append({
            "order_id": "ORD-1",
            "user_phone": "919876543210",
            "vehicle_id": "ACTIVA-01",
            "total_amount": 150.0,
            "status": OrderStatus.PENDING_PAYMENT.value
        })
        self.mock_db.payments.items.append({
            "order_id": "ORD-1",
            "payment_id": "PAY-1",
            "amount": 150.0,
            "status": PaymentStatus.PENDING.value,
            "idempotency_key": None
        })
        self.mock_db.vehicles.items.append({
            "vehicle_id": "ACTIVA-01",
            "name": "Honda Activa 6G",
            "registration_number": "AP02AB1234",
            "availability_status": VehicleStatus.HELD.value
        })
        self.mock_db.users.items.append({
            "phone_number": "919876543210",
            "state": "AWAITING_PAYMENT"
        })

    @patch("app.services.whatsapp.service.whatsapp_service.send_message", new_callable=AsyncMock)
    @patch("app.services.whatsapp.service.whatsapp_service.notify_admins", new_callable=AsyncMock)
    async def test_idempotent_payment_processing(self, mock_notify, mock_send):
        # 1. First webhook execution -> Must succeed and confirm order
        res1 = await process_verified_payment(
            order_id="ORD-1",
            payment_id="PAY-1",
            provider_payment_id="prov_123",
            idempotency_key="idemp_123",
            amount=150.0
        )
        self.assertEqual(res1["status"], "SUCCESS")

        # Verify database transitions
        order = await self.mock_db.orders.find_one({"order_id": "ORD-1"})
        self.assertEqual(order["status"], OrderStatus.CONFIRMED.value)

        vehicle = await self.mock_db.vehicles.find_one({"vehicle_id": "ACTIVA-01"})
        self.assertEqual(vehicle["availability_status"], VehicleStatus.BOOKED.value)

        payment = await self.mock_db.payments.find_one({"order_id": "ORD-1"})
        self.assertEqual(payment["status"], PaymentStatus.VERIFIED.value)

        # 2. Duplicate webhook replay -> Must return ALREADY_PROCESSED and NOT trigger duplicate bookings
        res2 = await process_verified_payment(
            order_id="ORD-1",
            payment_id="PAY-1",
            provider_payment_id="prov_123",
            idempotency_key="idemp_123",
            amount=150.0
        )
        self.assertEqual(res2["status"], "ALREADY_PROCESSED")

if __name__ == "__main__":
    unittest.main()
