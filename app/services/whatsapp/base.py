from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class WhatsAppClientInterface(ABC):
    """
    Abstract interface for WhatsApp messaging providers.
    Ensures complete decoupling between business logic and the underlying WhatsApp provider
    (e.g., local WhatsApp Web automation bridge vs official Meta WhatsApp Cloud API).
    """

    @abstractmethod
    async def send_text_message(self, to: str, message: str) -> bool:
        """Send a plain text message to a phone number."""
        pass

    @abstractmethod
    async def send_media_message(
        self,
        to: str,
        file_path: str,
        caption: str = "",
        mime_type: Optional[str] = None
    ) -> bool:
        """Send an image or document to a phone number."""
        pass

    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Check provider connection status."""
        pass

    async def forward_message(self, message_id: str, to: str) -> bool:
        """Forward an existing WhatsApp message directly in original quality."""
        return False
