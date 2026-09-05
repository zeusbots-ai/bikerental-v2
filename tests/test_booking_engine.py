import unittest
from app.models.order import DurationType
from app.services.booking.engine import calculate_rental_price

class TestBookingEngine(unittest.TestCase):

    def test_calculate_rental_price_hourly(self):
        vehicle = {
            "vehicle_id": "ACTIVA-01",
            "price_per_hour": 60.0,
            "price_per_day": 400.0
        }
        total = calculate_rental_price(vehicle, DurationType.HOURLY, 3.0)
        self.assertEqual(total, 180.0)

    def test_calculate_rental_price_daily(self):
        vehicle = {
            "vehicle_id": "ACTIVA-01",
            "price_per_hour": 60.0,
            "price_per_day": 350.0
        }
        total = calculate_rental_price(vehicle, DurationType.DAILY, 2.0)
        self.assertEqual(total, 700.0)

    def test_calculate_rental_price_fractional(self):
        vehicle = {
            "vehicle_id": "BIKE-01",
            "price_per_hour": 50.0,
            "price_per_day": 300.0
        }
        total = calculate_rental_price(vehicle, DurationType.HOURLY, 2.5)
        self.assertEqual(total, 125.0)

if __name__ == "__main__":
    unittest.main()
