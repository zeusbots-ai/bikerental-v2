import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from app.database import set_database
from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.models.vehicle import VehicleStatus
from app.services.booking.expiry_worker import check_and_release_expired_holds
from app.utils.time import utc_now

class MockCursor:
    def __init__(self, items):
        self.items = items

    async def to_list(self, length=100):
        return self.items

class MockHoldDB:
    def __init__(self):
        self.orders_list = []
        self.payments_list = []
        self.vehicles_list = []
        self.users_list = []
        self.audit_logs_list = []

    class MockCollection:
        def __init__(self, storage, name):
            self.storage = storage
            self.name = name

        def find(self, filter_dict):
            # Check for expired orders
            matches = []
            now = utc_now()
            for item in self.storage:
                if item.get("status") == OrderStatus.PENDING_PAYMENT.value:
                    if "hold_expires_at" in filter_dict and "$lt" in filter_dict["hold_expires_at"]:
                        if item.get("hold_expires_at") < filter_dict["hold_expires_at"]["$lt"]:
                            matches.append(item)
            return MockCursor(matches)

        async def find_one(self, filter_dict):
            for item in self.storage:
                if all(item.get(k) == v for k, v in filter_dict.items()):
                    return item
            return None

        async def update_one(self, filter_dict, update_dict, upsert=False):
            for item in self.storage:
                if all(item.get(k) == v for k, v in filter_dict.items()):
                    if "$set" in update_dict:
                        item.update(update_dict["$set"])
                    return type("Res", (), {"modified_count": 1})()
            return type("Res", (), {"modified_count": 0})()

        async def find_one_and_update(self, filter_dict, update_dict, return_document=False):
            for item in self.storage:
                if all(item.get(k) == v for k, v in filter_dict.items()):
                    if "$set" in update_dict:
                        item.update(update_dict["$set"])
                    return item
            return None

        async def insert_one(self, doc):
            self.storage.append(doc)
            return type("Res", (), {"inserted_id": "mock_id"})()

    @property
    def orders(self):
        return self.MockCollection(self.orders_list, "orders")

    @property
    def payments(self):
        return self.MockCollection(self.payments_list, "payments")

    @property
    def vehicles(self):
        return self.MockCollection(self.vehicles_list, "vehicles")

    @property
    def users(self):
        return self.MockCollection(self.users_list, "users")

    @property
    def audit_logs(self):
        return self.MockCollection(self.audit_logs_list, "audit_logs")

class TestHoldExpiry(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.mock_db = MockHoldDB()
        set_database(self.mock_db)

        now = utc_now()
        expired_time = now - timedelta(minutes=15) # Expired 15 mins ago

        self.mock_db.orders_list.append({
            "order_id": "ORD-EXP-1",
            "user_phone": "919876543210",
            "vehicle_id": "ACTIVA-01",
            "status": OrderStatus.PENDING_PAYMENT.value,
            "hold_expires_at": expired_time
        })
        self.mock_db.payments_list.append({
            "order_id": "ORD-EXP-1",
            "status": PaymentStatus.PENDING.value
        })
        self.mock_db.vehicles_list.append({
            "vehicle_id": "ACTIVA-01",
            "name": "Honda Activa 6G",
            "availability_status": VehicleStatus.HELD.value
        })

    @patch("app.services.whatsapp.service.whatsapp_service.send_message", new_callable=AsyncMock)
    @patch("app.services.whatsapp.service.whatsapp_service.notify_admins", new_callable=AsyncMock)
    async def test_hold_expiry_releases_vehicle(self, mock_notify, mock_send):
        await check_and_release_expired_holds()

        # Check vehicle is released back to AVAILABLE
        vehicle = await self.mock_db.vehicles.find_one({"vehicle_id": "ACTIVA-01"})
        self.assertEqual(vehicle["availability_status"], VehicleStatus.AVAILABLE.value)

        # Check order is marked EXPIRED
        order = await self.mock_db.orders.find_one({"order_id": "ORD-EXP-1"})
        self.assertEqual(order["status"], OrderStatus.EXPIRED.value)

        # Check payment is marked EXPIRED
        payment = await self.mock_db.payments.find_one({"order_id": "ORD-EXP-1"})
        self.assertEqual(payment["status"], PaymentStatus.EXPIRED.value)

if __name__ == "__main__":
    unittest.main()
