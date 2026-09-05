import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from app.models.user import UserState, VerificationStatus
from app.handlers.router import route_inbound_message
from app.handlers.customer_flow import CustomerFlowHandler
from app.services.verification.service import VerificationService
from app.services.whatsapp.service import WhatsAppService

class TestMediaVerificationFlow(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Create a temporary image file for tests
        self.temp_img = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        self.temp_img.write(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 50)
        self.temp_img.flush()
        self.temp_img.close()

        # Create a temporary PDF file for tests
        self.temp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        self.temp_pdf.write(b"%PDF-1.4\n" + b"\x00" * 50)
        self.temp_pdf.flush()
        self.temp_pdf.close()

    def tearDown(self):
        if os.path.exists(self.temp_img.name):
            os.remove(self.temp_img.name)
        if os.path.exists(self.temp_pdf.name):
            os.remove(self.temp_pdf.name)

    @patch("app.handlers.customer_flow.whatsapp_service.send_message", new_callable=AsyncMock)
    async def test_handle_id_card_submission_missing_media(self, mock_send):
        """When user sends text with no media attachment, prompt for photo."""
        await CustomerFlowHandler._handle_id_card_submission(
            phone="919876543210",
            has_media=False,
            media=None,
            raw_has_media=False
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("Photo Required", args[1])

    @patch("app.handlers.customer_flow.whatsapp_service.send_message", new_callable=AsyncMock)
    async def test_handle_id_card_submission_download_failed(self, mock_send):
        """When bridge detected media but download failed, inform user to resend."""
        await CustomerFlowHandler._handle_id_card_submission(
            phone="919876543210",
            has_media=False,
            media=None,
            raw_has_media=True
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("Media Download Failed", args[1])

    @patch("app.handlers.customer_flow.whatsapp_service.send_message", new_callable=AsyncMock)
    async def test_handle_id_card_submission_invalid_media_type(self, mock_send):
        """When user sends audio or video instead of photo/pdf, reject with clear error."""
        media = {
            "filePath": self.temp_img.name,
            "mimetype": "audio/ogg",
            "filename": "voice.ogg"
        }
        await CustomerFlowHandler._handle_id_card_submission(
            phone="919876543210",
            has_media=True,
            media=media,
            raw_has_media=True
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("Invalid File Type", args[1])
        self.assertIn("photo (JPG, PNG) or PDF document", args[1])

    @patch("app.handlers.customer_flow.whatsapp_service.send_message", new_callable=AsyncMock)
    @patch("app.services.verification.service.VerificationService.create_verification_request", new_callable=AsyncMock)
    async def test_handle_id_card_submission_valid_image(self, mock_create_ver, mock_send):
        """When user sends valid photo, create verification request and acknowledge."""
        mock_create_ver.return_value = {"verification_id": "VER-TEST-001"}
        media = {
            "filePath": self.temp_img.name,
            "mimetype": "image/jpeg",
            "filename": "student_id.jpg"
        }
        await CustomerFlowHandler._handle_id_card_submission(
            phone="919876543210",
            has_media=True,
            media=media,
            raw_has_media=True
        )
        mock_create_ver.assert_called_once_with(
            user_phone="919876543210",
            media_file_path=self.temp_img.name,
            mime_type="image/jpeg"
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("Verification Request Submitted", args[1])
        self.assertIn("VER-TEST-001", args[1])

    @patch("app.handlers.customer_flow.whatsapp_service.send_message", new_callable=AsyncMock)
    @patch("app.services.verification.service.VerificationService.create_verification_request", new_callable=AsyncMock)
    async def test_handle_id_card_submission_valid_pdf(self, mock_create_ver, mock_send):
        """When user sends student ID as PDF document, accept it."""
        mock_create_ver.return_value = {"verification_id": "VER-TEST-002"}
        media = {
            "filePath": self.temp_pdf.name,
            "mimetype": "application/pdf",
            "filename": "student_id.pdf"
        }
        await CustomerFlowHandler._handle_id_card_submission(
            phone="919876543210",
            has_media=True,
            media=media,
            raw_has_media=True
        )
        mock_create_ver.assert_called_once_with(
            user_phone="919876543210",
            media_file_path=self.temp_pdf.name,
            mime_type="application/pdf"
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("VER-TEST-002", args[1])

    @patch("app.services.verification.service.get_database")
    @patch("app.services.verification.service.whatsapp_service.notify_admins", new_callable=AsyncMock)
    async def test_verification_service_notifies_admins(self, mock_notify, mock_get_db):
        """VerificationService stores DB record and notifies admins with file path and mimetype."""
        mock_db = MagicMock()
        mock_db.verifications.insert_one = AsyncMock()
        mock_db.users.update_one = AsyncMock()
        mock_get_db.return_value = mock_db
        mock_notify.return_value = 1

        ver_doc = await VerificationService.create_verification_request(
            user_phone="919876543210",
            media_file_path=self.temp_img.name,
            mime_type="image/jpeg"
        )

        self.assertIn("verification_id", ver_doc)
        self.assertEqual(ver_doc["user_phone"], "919876543210")
        self.assertEqual(ver_doc["id_card_media_path"], self.temp_img.name)
        self.assertEqual(ver_doc["mime_type"], "image/jpeg")

        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        args = mock_notify.call_args.args
        alert_msg = args[0] if args else kwargs.get("message")
        file_path_arg = kwargs.get("file_path")
        mime_type_arg = kwargs.get("mime_type")

        self.assertIn("NEW CUAP VERIFICATION REQUEST", alert_msg)
        self.assertEqual(file_path_arg, self.temp_img.name)
        self.assertEqual(mime_type_arg, "image/jpeg")

    @patch("app.services.whatsapp.service.get_database", return_value=None)
    async def test_whatsapp_service_admin_fallback_on_media_failure(self, _):
        """If send_media returns False or throws, notify_admins falls back to text."""
        service = WhatsAppService()
        service.send_media = AsyncMock(return_value=False)
        service.send_message = AsyncMock(return_value=True)

        sent_count = await service.notify_admins(
            message="Test Alert",
            file_path=self.temp_img.name,
            mime_type="image/jpeg"
        )

        # send_media was attempted, but returned False -> send_message called as fallback
        service.send_media.assert_called()
        service.send_message.assert_called()
        self.assertGreaterEqual(sent_count, 1)

    @patch("app.services.whatsapp.service.get_database", return_value=None)
    async def test_whatsapp_service_admin_fallback_on_media_exception(self, _):
        """If send_media throws an exception, notify_admins falls back to text."""
        service = WhatsAppService()
        service.send_media = AsyncMock(side_effect=Exception("Bridge connection dropped"))
        service.send_message = AsyncMock(return_value=True)

        sent_count = await service.notify_admins(
            message="Test Alert",
            file_path=self.temp_img.name,
            mime_type="image/jpeg"
        )

        service.send_media.assert_called()
        service.send_message.assert_called()
        self.assertGreaterEqual(sent_count, 1)

    @patch("app.handlers.router.get_database", return_value=None)
    @patch("app.handlers.router.CustomerFlowHandler.handle_message", new_callable=AsyncMock)
    async def test_router_logging_and_forwarding(self, mock_handle, _):
        """Router properly logs media payload and passes parsed fields to CustomerFlowHandler."""
        payload = {
            "message_id": "msg_test_123",
            "sender_phone": "919876543210",
            "from": "919876543210@c.us",
            "body": "",
            "has_media": True,
            "raw_has_media": True,
            "media": {
                "filePath": self.temp_img.name,
                "mimetype": "image/jpeg",
                "filename": "test.jpg",
                "filesize": 1024
            }
        }

        await route_inbound_message(payload)
        mock_handle.assert_called_once()
        kwargs = mock_handle.call_args.kwargs
        self.assertEqual(kwargs.get("sender_phone"), "919876543210")
        self.assertTrue(kwargs.get("has_media"))
        self.assertTrue(kwargs.get("raw_has_media"))
        self.assertEqual(kwargs.get("media")["filePath"], self.temp_img.name)

if __name__ == "__main__":
    unittest.main()
