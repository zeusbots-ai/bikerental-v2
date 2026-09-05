import unittest
from unittest.mock import patch, AsyncMock
from app.admin.commands import is_admin_authorized, AdminCommandHandler

class TestAdminCommands(unittest.IsolatedAsyncioTestCase):

    async def test_admin_authorization(self):
        # 916371737949 is in settings.admin_phone_list
        authorized = await is_admin_authorized("916371737949")
        self.assertTrue(authorized)

        # Formats with + or spaces should also be handled cleanly
        authorized_with_plus = await is_admin_authorized("+91 6371737949")
        self.assertTrue(authorized_with_plus)

        # 10-digit format without 91 prefix
        authorized_ten_digit = await is_admin_authorized("6371737949")
        self.assertTrue(authorized_ten_digit)

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
        response = await AdminCommandHandler.handle_command("916371737949", "/help")
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
    async def test_bridge_client_resolve_target(self):
        from app.services.whatsapp.bridge_client import _resolve_target
        mock_db = AsyncMock()
        mock_db.admins.find_one.return_value = {"phone_number": "916371737949", "wa_jid": "162947334668337@lid"}
        with patch("app.services.whatsapp.bridge_client.get_database", return_value=mock_db):
            target = await _resolve_target("6371737949")
            self.assertEqual(target, "162947334668337@lid")

            # Preserves full JID if already @lid
            lid_target = await _resolve_target("186896156205308@lid")
            self.assertEqual(lid_target, "186896156205308@lid")

    async def test_get_admin_targets_resolves_lid(self):
        from app.services.whatsapp.service import _get_admin_targets
        mock_db = AsyncMock()
        # Mock cursor for db.admins.find
        class AsyncCursor:
            def __init__(self, docs):
                self.docs = docs
            def __aiter__(self):
                self._iter = iter(self.docs)
                return self
            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        mock_db.admins.find = unittest.mock.MagicMock(return_value=AsyncCursor([
            {"phone_number": "916371737949", "wa_jid": "162947334668337@lid", "is_active": True},
            {"phone_number": "919876543210", "is_active": True}
        ]))
        mock_db.admins.find_one.side_effect = lambda query: {"wa_jid": "162947334668337@lid"} if "916371737949" in str(query) or "6371737949" in str(query) else None
        mock_db.users.find_one.return_value = None

        with patch("app.services.whatsapp.service.get_database", return_value=mock_db):
            targets = await _get_admin_targets()
            self.assertIn("162947334668337@lid", targets)

if __name__ == "__main__":
    unittest.main()

