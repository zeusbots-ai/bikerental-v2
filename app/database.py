import logging
from datetime import datetime, timezone
from typing import Any
from app.config import settings

logger = logging.getLogger(__name__)

try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
except ImportError:
    AsyncIOMotorClient = Any
    AsyncIOMotorDatabase = Any

class Database:
    client: Any = None
    db: Any = None

db_manager = Database()

def get_database() -> Any:
    return db_manager.db

def set_database(custom_db: Any):
    """Allows injection of mock databases for testing."""
    db_manager.db = custom_db

async def init_db():
    if AsyncIOMotorClient is Any:
        logger.warning("Motor is not installed. Database connection skipped.")
        return

    logger.info(f"Connecting to MongoDB at {settings.MONGODB_URI.split('@')[-1] if '@' in settings.MONGODB_URI else 'localhost'}...")
    db_manager.client = AsyncIOMotorClient(settings.MONGODB_URI)
    db_manager.db = db_manager.client[settings.MONGODB_DB_NAME]

    # Create Indexes
    await db_manager.db.users.create_index("phone_number", unique=True)
    await db_manager.db.verifications.create_index("verification_id", unique=True)
    await db_manager.db.verifications.create_index("user_phone")
    await db_manager.db.vehicles.create_index("vehicle_id", unique=True)
    await db_manager.db.vehicles.create_index("availability_status")
    await db_manager.db.orders.create_index("order_id", unique=True)
    await db_manager.db.orders.create_index("user_phone")
    await db_manager.db.orders.create_index("status")
    await db_manager.db.orders.create_index("hold_expires_at")
    await db_manager.db.payments.create_index("payment_id", unique=True)
    await db_manager.db.payments.create_index("order_id")
    await db_manager.db.payments.create_index("idempotency_key", unique=True, sparse=True)
    await db_manager.db.admins.create_index("phone_number", unique=True)
    await db_manager.db.audit_logs.create_index("timestamp")

    # Initialize default settings if not existing
    current_settings = await db_manager.db.settings.find_one({"key": "system_config"})
    if not current_settings:
        await db_manager.db.settings.insert_one({
            "key": "system_config",
            "is_booking_enabled": True,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "system"
        })
        logger.info("Initialized default system_config (is_booking_enabled=True).")

    # Bootstrap default admins from environment if not present
    for phone in settings.admin_phone_list:
        existing_admin = await db_manager.db.admins.find_one({"phone_number": phone})
        if not existing_admin:
            await db_manager.db.admins.insert_one({
                "phone_number": phone,
                "name": "Bootstrap Admin",
                "role": "SUPERADMIN",
                "is_active": True,
                "created_at": datetime.now(timezone.utc)
            })
            logger.info(f"Bootstrapped superadmin: {phone}")

    logger.info("MongoDB indexes and initialization completed successfully.")

async def close_db():
    if db_manager.client and hasattr(db_manager.client, "close"):
        db_manager.client.close()
        logger.info("MongoDB connection closed.")
