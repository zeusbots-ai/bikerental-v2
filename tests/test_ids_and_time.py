import re
import unittest
from datetime import datetime, timezone
from app.utils.ids import generate_verification_id, generate_order_id, generate_payment_id
from app.utils.time import format_ist, utc_now

class TestIdsAndTime(unittest.TestCase):

    def test_verification_id_format(self):
        ver_id = generate_verification_id()
        pattern = r"^VER-[A-HJ-NP-Z2-9]{4}$"
        self.assertTrue(re.match(pattern, ver_id), f"Invalid verification ID format: {ver_id}")

    def test_order_id_format(self):
        ord_id = generate_order_id()
        pattern = r"^ORD-[A-HJ-NP-Z2-9]{4}$"
        self.assertTrue(re.match(pattern, ord_id), f"Invalid order ID format: {ord_id}")

    def test_payment_id_format(self):
        pay_id = generate_payment_id()
        pattern = r"^PAY-[A-HJ-NP-Z2-9]{4}$"
        self.assertTrue(re.match(pattern, pay_id), f"Invalid payment ID format: {pay_id}")

    def test_format_ist(self):
        # 2026-09-02 12:00:00 UTC should be 17:30:00 IST (UTC+5:30)
        dt_utc = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        ist_str = format_ist(dt_utc)
        self.assertIn("02 Sep 2026", ist_str)
        self.assertIn("05:30 PM IST", ist_str)

    def test_normalize_phone(self):
        from app.config import normalize_phone
        # 10 digit Indian number
        self.assertEqual(normalize_phone("8418894051"), "918418894051")
        self.assertEqual(normalize_phone("6371737949"), "916371737949")
        # 10 digit with +91 or spaces
        self.assertEqual(normalize_phone("+91 84188 94051"), "918418894051")
        # 11 digit with leading zero
        self.assertEqual(normalize_phone("08418894051"), "918418894051")
        # JID format preserved
        self.assertEqual(normalize_phone("162947334668337@lid"), "162947334668337@lid")
        self.assertEqual(normalize_phone("916371737949@c.us"), "916371737949@c.us")

if __name__ == "__main__":
    unittest.main()
