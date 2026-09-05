# 🛵 CUAP Wheels - Hostel Bike & Scooty Rental WhatsApp Bot

A production-grade WhatsApp bot for hostel bike and scooty rentals at the **Central University of Andhra Pradesh (CUAP)**, using a **normal personal WhatsApp account** via WhatsApp Web automation (no Meta WhatsApp Business Cloud API required).

---

## 📑 Table of Contents
1. [Architecture Overview](#-architecture-overview)
2. [WhatsApp Web Automation & Limitations](#-whatsapp-web-automation--limitations)
3. [How QR Code Login Works](#-how-qr-code-login-works)
4. [Railway Deployment & Session Persistence](#-railway-deployment--session-persistence)
5. [Customer Journey & Verification Flow](#-customer-journey--verification-flow)
6. [Multi-Admin Command Center](#-multi-admin-command-center)
7. [Pluggable Payment Gateway & Idempotency](#-pluggable-payment-gateway--idempotency)
8. [Database Collections & Schemas](#-database-collections--schemas)
9. [Project Directory Structure](#-project-directory-structure)
10. [Local Development Setup](#-local-development-setup)
11. [Running Tests](#-running-tests)

---

## 🏗️ Architecture Overview

The system uses a **decoupled dual-process architecture**:

```
+-------------------------------------------------------------+
|                     User / Admin Phone                      |
+-------------------------------------------------------------+
                              |
                     WhatsApp Protocol
                              v
+-------------------------------------------------------------+
|         WhatsApp Web Bridge (Node.js / whatsapp-web.js)      |
|  - Puppeteer headless Chromium                              |
|  - LocalAuth multi-device session caching                   |
|  - Exposes: POST /send-message, /send-media, GET /qr        |
+-------------------------------------------------------------+
       | HTTP Webhook                        ^ HTTP REST
       v                                     |
+-------------------------------------------------------------+
|                 FastAPI Core Backend (Python)               |
|  - Inbound Message Router & State Machine                   |
|  - Admin Authorization & Slash Command Processor            |
|  - Atomic Booking Engine (Find-One-And-Update Concurrency)  |
|  - 10-Minute Hold Expiry Background Worker                  |
|  - Pluggable Payment Gateway (Mock / Razorpay Webhooks)     |
+-------------------------------------------------------------+
       |                                     |
       v                                     v
+-----------------------+         +---------------------------+
|     MongoDB Atlas     |         |  Payment Gateway Webhook  |
| (Async Motor Driver)  |         | (HMAC Signature Verified) |
+-----------------------+         +---------------------------+
```

### Why Decouple WhatsApp from Business Logic?
The WhatsApp integration layer (`app/services/whatsapp/`) is abstracted behind `WhatsAppClientInterface`. Today it calls the local `whatsapp-web.js` bridge. If you migrate to the official Meta Cloud API later, you simply switch `WHATSAPP_PROVIDER=cloud` in `.env` without modifying any business logic or handlers.

---

## ⚠️ WhatsApp Web Automation & Limitations

Using a personal WhatsApp account for automation involves distinct trade-offs compared to the official Business API:

1. **Meta Anti-Abuse Algorithms**:
   - Meta monitors automated patterns. Rapid-fire messaging to strangers can trigger account bans.
   - *Mitigation*: Our bridge implements randomized human-like typing jitter (300ms–800ms delay), strictly responds only to inbound customer messages, and restricts admin commands to authorized numbers.
2. **Linked Device Limits**:
   - WhatsApp Multi-Device permits up to 4 linked companion devices.
   - Do not log out from the companion device under WhatsApp *Settings > Linked Devices* on your phone.
3. **Chromium Footprint**:
   - Headless Chromium requires ~350MB–600MB of RAM. The provided Dockerfile configures flags (`--no-sandbox`, `--disable-dev-shm-usage`, `--disable-gpu`) optimized for container platforms like Railway.
4. **Single-Process Session Locking**:
   - Chromium's IndexedDB/LevelDB storage locks session files. Run as a single replica (`replicas: 1`).

---

## 📱 How QR Code Login Works

1. **First Startup**:
   When started without a session in the persistent folder, `whatsapp-web.js` connects to WhatsApp and receives a pairing token.
2. **Dual Display**:
   - **Terminal ANSI View**: Displayed directly in your deploy logs via `qrcode-terminal`.
   - **Web Browser View**: A live-updating web page is available at:
     ```
     https://<your-app-domain>.up.railway.app/qr
     ```
3. **Scanning**:
   Open WhatsApp on your phone &rarr; **Settings / Menu** &rarr; **Linked Devices** &rarr; **Link a Device** &rarr; Scan the QR.
4. **Session Persistence**:
   Upon authentication, cryptographic keys and tokens are saved to `/data/session`.
5. **Future Restarts**:
   On future restarts or deployments, `LocalAuth` loads the session directly from disk. **No QR scan is required on restarts.**

---

## 🚂 Railway Deployment & Session Persistence

### The Ephemeral Filesystem Problem
Railway containers use ephemeral filesystems by default. Without a persistent volume, every redeploy wipes the session directory, forcing a new QR scan every time.

### Persistent Volume Configuration (Crucial Step)
1. In your **Railway Dashboard**, select your project service.
2. Navigate to **Settings** &rarr; **Volumes** &rarr; click **Add Volume**.
3. Set the **Mount Path** to:
   ```
   /data
   ```
4. Configure your Railway Environment Variables:
   ```bash
   SESSION_DATA_PATH=/data/session
   MEDIA_STORAGE_PATH=/data/media
   PORT=8000
   BRIDGE_PORT=3001
   MONGODB_URI=mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/hostel_rental_db
   MONGODB_DB_NAME=hostel_rental_db
   ADMIN_PHONE_NUMBERS=919876543210
   WHATSAPP_PROVIDER=web
   PAYMENT_PROVIDER=mock # or razorpay
   ```

Railway will mount the persistent disk at `/data`. Container redeployments will preserve both your authenticated WhatsApp session and student ID uploads.

---

## 🔄 Customer Journey & Verification Flow

1. **First Contact**: Customer sends *"Hi"* or any greeting.
2. **Student Verification**: Bot asks:
   > *"Are you a CUAP student? Reply 1 for YES, 2 for NO"*
3. **ID Submission**:
   - If YES, customer sends a photo of their CUAP Student ID card.
   - Bot generates a unique tracking ID: `VER-YYYYMMDD-XXXX`.
   - Bot forwards the photo to all configured admin WhatsApp numbers.
4. **Admin Review**:
   - Admin reviews and replies: `/approve VER-YYYYMMDD-XXXX` or `/reject VER-YYYYMMDD-XXXX [reason]`.
5. **Booking**:
   - Once approved, the customer is shown the active vehicle fleet catalog.
   - Customer chooses vehicle (e.g. `1` or `ACTIVA-01`).
   - Customer chooses rental date and duration (Hourly or Daily).
   - Bot atomically holds the vehicle for **10 minutes** (`HELD` status).
   - Generates unique order ID: `ORD-YYYYMMDD-XXXX` and payment link.
6. **Payment**:
   - Customer clicks the secure payment link.
   - If unpaid after 10 minutes, the background worker automatically releases the vehicle back to `AVAILABLE` and alerts the customer and admins.
   - Upon successful payment callback, order is marked `CONFIRMED`, vehicle is marked `BOOKED`, and pickup instructions are sent.

---

## 🛠️ Multi-Admin Command Center

Only numbers registered in `ADMIN_PHONE_NUMBERS` or the MongoDB `admins` collection can execute slash commands. Every action is recorded in `audit_logs`.

| Command | Description |
| :--- | :--- |
| `/start` | Allow new customer bookings. |
| `/end` | Stop accepting new bookings (existing continue). |
| `/status` | View system health, fleet counts, pending verifications. |
| `/approve <VER-ID>` | Approve student verification and notify student with fleet catalog. |
| `/reject <VER-ID> [reason]` | Reject verification with feedback and allow re-attempt. |
| `/vehicles` | View all fleet vehicles, rates, and statuses. |
| `/available` | View only currently available fleet. |
| `/addvehicle <Name> \| <Type> \| <RegNo> \| <₹/hr> \| <₹/day> \| <Desc>` | Add a vehicle. |
| `/editvehicle <VEH-ID> <field> <value>` | Edit vehicle attributes (`price_hr`, `price_day`, `status`, `name`). |
| `/removevehicle <VEH-ID>` | Set vehicle to `MAINTENANCE`. |
| `/orders` | View recent 10 orders with status and timestamps. |
| `/order <ORD-ID>` | View complete details of a specific order. |
| `/complete <ORD-ID>` | Mark vehicle as returned and order `COMPLETED`. |
| `/cancel <ORD-ID> [reason]` | Cancel booking and release vehicle to `AVAILABLE`. |
| `/payment <ORD-ID>` | Check payment gateway status. |
| `/stats` | View booking counts, student counts, and total revenue. |

---

## 💳 Pluggable Payment Gateway & Idempotency

### Screenshot Policy
The bot strictly **does not accept payment screenshots** as proof of payment. Screenshots trigger an automated warning instructing the student to use the verified payment link.

### Webhook Idempotency
Payment providers (such as Razorpay) can resend webhooks during network retries. Our service ensures idempotency through atomic database updates:
```python
update_res = await db.payments.find_one_and_update(
    {"order_id": order_id, "status": "PENDING"},
    {"$set": {"status": "VERIFIED", "verified_at": now, "provider_payment_id": pid}}
)
```
If the payment is already `VERIFIED`, subsequent callbacks immediately return HTTP 200 `ALREADY_PROCESSED` without double-booking or sending duplicate WhatsApp messages.

---

## 🗄️ Database Collections & Schemas

- **`users`**: Customer phone, verification status (`UNVERIFIED`, `PENDING`, `APPROVED`, `REJECTED`), conversation state machine, temporary booking session.
- **`verifications`**: `verification_id` (`VER-YYYYMMDD-XXXX`), user phone, uploaded ID photo path, status, reviewed by admin, review timestamp.
- **`vehicles`**: `vehicle_id`, name, type (`SCOOTY`/`BIKE`), registration number, prices, `availability_status` (`AVAILABLE`, `HELD`, `BOOKED`, `RENTED`, `MAINTENANCE`).
- **`orders`**: `order_id` (`ORD-YYYYMMDD-XXXX`), user phone, vehicle ID, rental date, duration, total amount, `hold_expires_at`, status (`PENDING_PAYMENT`, `CONFIRMED`, `CANCELLED`, `EXPIRED`, `COMPLETED`).
- **`payments`**: `payment_id`, `order_id`, amount, status, provider, idempotency key, verified timestamp.
- **`admins`**: Authorized admin phone numbers, names, and roles (`SUPERADMIN`, `ADMIN`).
- **`settings`**: Global system configuration (`is_booking_enabled`).
- **`audit_logs`**: Immutable ledger of all admin actions with timestamps.

---

## 💻 Local Development Setup

### 1. Clone & Configure
```bash
cd /path/to/hostel-rental-bot
cp .env.example .env
```
Edit `.env` and set your admin phone number and MongoDB connection string.

### 2. Run Node.js Bridge
```bash
cd bridge
npm install
npm start
```

### 3. Run FastAPI Backend
In a separate terminal:
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Scan QR Code
- Scan the QR code rendered in the bridge terminal, or open `http://localhost:8000/qr` in your browser.

---

## 🧪 Running Tests

The test suite runs with standard Python:
```bash
python3 -m unittest discover tests
```
Included tests:
- `test_booking_engine.py`: Hourly and daily price calculations.
- `test_ids_and_time.py`: ID format generators and IST timezone formatting.
- `test_admin_commands.py`: Admin authorization and command dispatch.
- `test_payment_logic.py`: Gateway link creation and signature verification.
- `test_payment_idempotency.py`: Idempotent payment webhook processing.
- `test_hold_expiry.py`: Automated 10-minute hold release worker.
