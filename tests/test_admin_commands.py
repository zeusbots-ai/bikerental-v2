import unittest
from unittest.mock import patch, AsyncMock
from app.admin.commands import is_admin_authorized, AdminCommandHandler

class TestAdminCommands(unittest.IsolatedAsyncioTestCase):

    async def test_admin_authorization(self):
        # 919876543210 is in settings.admin_phone_list
        authorized = await is_admin_authorized("919876543210")
        self.assertTrue(authorized)

        # Formats with + or spaces should also be handled cleanly
        authorized_with_plus = await is_admin_authorized("+91 9876543210")
        self.assertTrue(authorized_with_plus)

        # Random user phone should not be authorized
        unauthorized = await is_admin_authorized("919999999999")
        self.assertFalse(unauthorized)

    async def test_unauthorized_command_execution(self):
        response = await AdminCommandHandler.handle_command("919999999999", "/status")
        self.assertIn("Unauthorized", response)

    async def test_user_admin_authorization(self):
        # 916371737949 is in settings.admin_phone_list
        authorized = await is_admin_authorized("916371737949")
        self.assertTrue(authorized)

    async def test_admin_authorization_with_jid_fallback(self):
        # Even if phone is an LID string, if DB has mapped wa_jid to an admin phone, it should authorize
        mock_db = AsyncMock()
        mock_db.admins.find_one.return_value = None
        mock_db.users.find_one.return_value = {"phone_number": "916371737949", "wa_jid": "162947334668337@lid"}

        with patch("app.admin.commands.get_database", return_value=mock_db):
            # Incoming phone is the raw LID number, but sender_jid links to admin
            authorized = await is_admin_authorized("162947334668337", sender_jid="162947334668337@lid")
            self.assertTrue(authorized)

            # Execution with command handler
            resp = await AdminCommandHandler.handle_command("162947334668337", "/help", sender_jid="162947334668337@lid")
            self.assertIn("Admin Command Center", resp)

    async def test_help_command(self):
        response = await AdminCommandHandler.handle_command("919876543210", "/help")
        self.assertIn("Admin Command Center", response)
        self.assertIn("/start", response)
        self.assertIn("/end", response)
        self.assertIn("/approve", response)
        self.assertIn("/reject", response)

    async def test_swipe_reply_approve_with_quoted_id(self):
        quoted_msg = {
            "message_id": "MSG_ADMIN_ALERT_1",
            "body": "🆔 *CUAP Student Verification Request*\n*Verification ID:* `VER-9A2K`\n*Student Phone:* `+919876543210`"
        }
        with patch("app.admin.commands.VerificationService.approve_verification", new=AsyncMock(return_value=(True, "✅ Verification `VER-9A2K` APPROVED"))) as mock_approve:
            resp = await AdminCommandHandler.handle_command(
                "916371737949",
                "/approve",
                quoted_message=quoted_msg
            )
            mock_approve.assert_called_once_with("916371737949", "VER-9A2K")
            self.assertIn("APPROVED", resp)

    async def test_swipe_reply_reject_with_quoted_id_and_reason(self):
        quoted_msg = {
            "message_id": "MSG_ADMIN_ALERT_2",
            "body": "🆔 *Verification ID:* `VER-7B3Q`\n*Student Phone:* `+919876543210`"
        }
        with patch("app.admin.commands.VerificationService.reject_verification", new=AsyncMock(return_value=(True, "❌ Verification `VER-7B3Q` REJECTED"))) as mock_reject:
            resp = await AdminCommandHandler.handle_command(
                "916371737949",
                "/reject photo is blurry",
                quoted_message=quoted_msg
            )
            mock_reject.assert_called_once_with("916371737949", "VER-7B3Q", "photo is blurry")
            self.assertIn("REJECTED", resp)

    async def test_approve_fallback_to_latest_pending(self):
        mock_db = AsyncMock()
        mock_db.verifications.find_one.return_value = {
            "_id": "dummy_id",
            "verification_id": "VER-4X9Z",
            "status": "PENDING"
        }
        with patch("app.admin.commands.get_database", return_value=mock_db), \
             patch("app.admin.commands.VerificationService.approve_verification", new=AsyncMock(return_value=(True, "✅ Verification `VER-4X9Z` APPROVED"))) as mock_approve:
            resp = await AdminCommandHandler.handle_command(
                "916371737949",
                "/approve"
            )
            mock_approve.assert_called_once_with("916371737949", "VER-4X9Z")
            self.assertIn("APPROVED", resp)

if __name__ == "__main__":
    unittest.main()

