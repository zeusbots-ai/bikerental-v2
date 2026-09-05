import os
from typing import List

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore"
        )
        ENVIRONMENT: str = "development"
        PORT: int = 8000
        HOST: str = "0.0.0.0"
        TIMEZONE: str = "Asia/Kolkata"
        BRIDGE_PORT: int = 3001
        BRIDGE_URL: str = "http://localhost:3001"
        FASTAPI_WEBHOOK_URL: str = "http://localhost:8000/api/v1/whatsapp/webhook"
        MONGODB_URI: str = "mongodb+srv://rishu:Khushi@cluster0.3o5g1pe.mongodb.net/?appName=Cluster0"
        MONGODB_DB_NAME: str = "hostel_rental_db"
        ADMIN_PHONE_NUMBERS: str = "916371737949,919876543210"
        WHATSAPP_PROVIDER: str = "web"
        PAYMENT_PROVIDER: str = "mock"
        RAZORPAY_KEY_ID: str = ""
        RAZORPAY_KEY_SECRET: str = ""
        RAZORPAY_WEBHOOK_SECRET: str = ""
        SESSION_DATA_PATH: str = "./data/session"
        MEDIA_STORAGE_PATH: str = "./data/media"
        HOLD_EXPIRY_MINUTES: int = 10
        BOT_NAME: str = "CUAP Wheels"
        HOSTEL_NAME: str = "Central University of Andhra Pradesh Hostel"

        @property
        def admin_phone_list(self) -> List[str]:
            return [
                phone.strip().replace("+", "").replace(" ", "").replace("-", "")
                for phone in self.ADMIN_PHONE_NUMBERS.split(",")
                if phone.strip()
            ]
except ImportError:
    from pydantic import BaseModel
    class Settings(BaseModel):
        ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
        PORT: int = int(os.getenv("PORT", "8000"))
        HOST: str = os.getenv("HOST", "0.0.0.0")
        TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Kolkata")
        BRIDGE_PORT: int = int(os.getenv("BRIDGE_PORT", "3001"))
        BRIDGE_URL: str = os.getenv("BRIDGE_URL", "http://localhost:3001")
        FASTAPI_WEBHOOK_URL: str = os.getenv("FASTAPI_WEBHOOK_URL", "http://localhost:8000/api/v1/whatsapp/webhook")
        MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "hostel_rental_db")
        ADMIN_PHONE_NUMBERS: str = os.getenv("ADMIN_PHONE_NUMBERS", "916371737949,919876543210")
        WHATSAPP_PROVIDER: str = os.getenv("WHATSAPP_PROVIDER", "web")
        PAYMENT_PROVIDER: str = os.getenv("PAYMENT_PROVIDER", "mock")
        RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
        RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")
        RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
        SESSION_DATA_PATH: str = os.getenv("SESSION_DATA_PATH", "./data/session")
        MEDIA_STORAGE_PATH: str = os.getenv("MEDIA_STORAGE_PATH", "./data/media")
        HOLD_EXPIRY_MINUTES: int = int(os.getenv("HOLD_EXPIRY_MINUTES", "10"))
        BOT_NAME: str = os.getenv("BOT_NAME", "CUAP Wheels")
        HOSTEL_NAME: str = os.getenv("HOSTEL_NAME", "Central University of Andhra Pradesh Hostel")

        @property
        def admin_phone_list(self) -> List[str]:
            return [
                phone.strip().replace("+", "").replace(" ", "").replace("-", "")
                for phone in self.ADMIN_PHONE_NUMBERS.split(",")
                if phone.strip()
            ]

settings = Settings()
