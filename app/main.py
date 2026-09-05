import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from app.config import settings
from app.database import init_db, close_db, get_database
from app.handlers.router import route_inbound_message
from app.services.booking.expiry_worker import run_hold_expiry_worker
from app.services.payments.service import get_payment_gateway, process_verified_payment
from app.services.whatsapp.service import whatsapp_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("hostel_rental_bot")

expiry_task: asyncio.Task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting CUAP Hostel Bike Rental Application...")
    try:
        await init_db()
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB on startup: {e}")

    # Launch background hold expiry worker
    global expiry_task
    expiry_task = asyncio.create_task(run_hold_expiry_worker(interval_seconds=30))
    logger.info("Started 10-minute hold expiry background worker.")

    yield

    # Shutdown
    if expiry_task and not expiry_task.done():
        expiry_task.cancel()
    await close_db()
    logger.info("Application shutdown complete.")

app = FastAPI(
    title="CUAP Hostel Bike & Scooty Rental Bot",
    description="WhatsApp-based hostel vehicle booking service for CUAP students",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/", response_class=JSONResponse)
async def root():
    return {
        "service": "CUAP Hostel Bike & Scooty Rental Bot",
        "status": "online",
        "whatsapp_provider": settings.WHATSAPP_PROVIDER,
        "payment_provider": settings.PAYMENT_PROVIDER,
        "docs": "/docs",
        "health": "/health",
        "qr_login": "/qr"
    }

@app.get("/health", response_class=JSONResponse)
async def health():
    db_ok = False
    db = get_database()
    if db is not None:
        try:
            await db.command("ping")
            db_ok = True
        except Exception:
            db_ok = False

    wa_status = await whatsapp_service.get_status()

    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "whatsapp_bridge": wa_status,
        "environment": settings.ENVIRONMENT
    }

@app.get("/qr", response_class=HTMLResponse)
async def view_qr():
    """
    Renders the live WhatsApp Web pairing QR code.
    Accessible from Railway browser window if terminal logs are difficult to scan.
    """
    qr_data = await whatsapp_service.get_qr_data()
    wa_status = qr_data.get("status", "INITIALIZING")
    data_url = qr_data.get("dataUrl")

    if wa_status == "READY":
        return HTMLResponse("""
            <!DOCTYPE html>
            <html>
            <head><title>WhatsApp Bot - Connected</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
            <body style="font-family: -apple-system, sans-serif; text-align: center; padding: 60px 20px; background: #f0f2f5;">
                <div style="max-width: 450px; margin: auto; background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08);">
                    <div style="font-size: 54px; margin-bottom: 10px;">✅</div>
                    <h2 style="color: #075e54; margin: 0 0 10px;">WhatsApp Bot is Connected!</h2>
                    <p style="color: #54656f;">The session is active and stored securely in persistent storage.</p>
                </div>
            </body>
            </html>
        """)

    if not data_url:
        return HTMLResponse(f"""
            <!DOCTYPE html>
            <html>
            <head><title>WhatsApp Bot - Initializing</title><meta http-equiv="refresh" content="3"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
            <body style="font-family: -apple-system, sans-serif; text-align: center; padding: 60px 20px; background: #f0f2f5;">
                <div style="max-width: 450px; margin: auto; background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08);">
                    <h3 style="color: #3b4a54;">Starting WhatsApp Web Session...</h3>
                    <p style="color: #54656f;">Status: <b>{wa_status}</b></p>
                    <p style="font-size: 14px; color: #8696a0;">Generating pairing token. This page refreshes automatically...</p>
                </div>
            </body>
            </html>
        """)

    return HTMLResponse(f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Scan WhatsApp QR - CUAP Wheels</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #ece5dd; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
                .card {{ background: white; padding: 30px; border-radius: 16px; box-shadow: 0 6px 24px rgba(0,0,0,0.12); text-align: center; max-width: 420px; width: 100%; }}
                h2 {{ color: #075e54; margin: 0 0 8px; }}
                .badge {{ display: inline-block; background: #e7f8e9; color: #1b5e20; padding: 4px 12px; border-radius: 20px; font-weight: 600; font-size: 13px; }}
                img {{ border: 4px solid #128c7e; border-radius: 12px; margin: 20px 0; max-width: 280px; width: 100%; }}
                ol {{ text-align: left; font-size: 14px; color: #3b4a54; line-height: 1.7; padding-left: 20px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Scan WhatsApp QR</h2>
                <span class="badge">{wa_status}</span>
                <div>
                    <img src="{data_url}" alt="WhatsApp Web Pairing QR" />
                </div>
                <ol>
                    <li>Open <b>WhatsApp</b> on your mobile phone</li>
                    <li>Tap <b>Menu (⋮)</b> or <b>Settings</b> &rarr; <b>Linked Devices</b></li>
                    <li>Tap <b>Link a Device</b> and point your camera at this QR code</li>
                </ol>
                <script>
                    setInterval(async () => {{
                        try {{
                            const res = await fetch('/health');
                            const data = await res.json();
                            if (data.whatsapp_bridge && data.whatsapp_bridge.status === 'READY') {{
                                location.reload();
                            }}
                        }} catch(e) {{}}
                    }}, 4000);
                </script>
            </div>
        </body>
        </html>
    """)

@app.post("/api/v1/whatsapp/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook endpoint receiving inbound messages and media events from whatsapp-web.js bridge.
    """
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Non-blocking async queue
    background_tasks.add_task(route_inbound_message, payload)
    return {"status": "queued"}

@app.post("/api/v1/payments/webhook")
async def payment_webhook(request: Request):
    """
    Provider-verified payment webhook callback (e.g. Razorpay).
    Guarantees idempotent processing and prevents duplicate bookings.
    """
    body_bytes = await request.body()
    headers = dict(request.headers)

    gateway = get_payment_gateway()

    # 1. Cryptographic Signature Verification
    if not gateway.verify_webhook_signature(headers, body_bytes):
        logger.warning("[PaymentWebhook] Webhook signature verification failed!")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    # 2. Parse Normalized Payment Payload
    parsed = gateway.parse_webhook_payload(payload)
    order_id = parsed.get("order_id")
    payment_id = parsed.get("payment_id")
    provider_payment_id = parsed.get("provider_payment_id")
    idempotency_key = parsed.get("idempotency_key")
    amount = parsed.get("amount", 0.0)
    status = parsed.get("status")

    if not order_id or status != "VERIFIED":
        logger.info(f"[PaymentWebhook] Ignored payment event with status: {status} for order: {order_id}")
        return {"status": "ignored", "reason": status}

    # 3. Idempotent Processing
    result = await process_verified_payment(
        order_id=order_id,
        payment_id=payment_id,
        provider_payment_id=provider_payment_id,
        idempotency_key=idempotency_key,
        amount=amount
    )

    return {"status": "success", "result": result}

@app.get("/api/v1/payments/mock-checkout", response_class=HTMLResponse)
async def mock_checkout(order_id: str, payment_id: str, amount: str):
    """
    Simulated checkout page for local development and testing.
    """
    return HTMLResponse(f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Mock Payment Gateway - CUAP Wheels</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f4f6f8; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
                .card {{ background: white; max-width: 420px; width: 100%; padding: 30px; border-radius: 14px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); text-align: center; }}
                h2 {{ color: #1e293b; margin-top: 0; }}
                .price {{ font-size: 32px; font-weight: 700; color: #0f172a; margin: 15px 0; }}
                .details {{ text-align: left; background: #f8fafc; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; color: #475569; }}
                .btn {{ background: #16a34a; color: white; border: none; padding: 14px 24px; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; width: 100%; transition: background 0.2s; }}
                .btn:hover {{ background: #15803d; }}
                .btn:disabled {{ background: #94a3b8; cursor: not-allowed; }}
                .note {{ font-size: 12px; color: #64748b; margin-top: 15px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Hostel Vehicle Rental</h2>
                <div class="price">₹{amount}</div>
                <div class="details">
                    <div><b>Order ID:</b> {order_id}</div>
                    <div><b>Payment ID:</b> {payment_id}</div>
                    <div><b>Gateway:</b> Mock Payment Gateway (Test Mode)</div>
                </div>
                <button id="payBtn" class="btn" onclick="triggerPayment()">Simulate Successful Payment</button>
                <div id="statusMsg" class="note">Click above to simulate an automated UPI/Card confirmation webhook.</div>
            </div>
            <script>
                async function triggerPayment() {{
                    const btn = document.getElementById('payBtn');
                    const msg = document.getElementById('statusMsg');
                    btn.disabled = true;
                    btn.innerText = 'Processing...';
                    try {{
                        const res = await fetch('/api/v1/payments/mock-webhook', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{
                                order_id: '{order_id}',
                                payment_id: '{payment_id}',
                                amount: {amount},
                                status: 'success'
                            }})
                        }});
                        const data = await res.json();
                        if (res.ok) {{
                            btn.innerText = 'Payment Successful! 🎉';
                            btn.style.background = '#059669';
                            msg.innerHTML = '<span style="color:#059669;font-weight:600;">Payment confirmed! Check your WhatsApp for booking confirmation.</span>';
                        }} else {{
                            btn.disabled = false;
                            btn.innerText = 'Retry Payment';
                            msg.innerText = 'Error: ' + JSON.stringify(data);
                        }}
                    }} catch (e) {{
                        btn.disabled = false;
                        btn.innerText = 'Retry Payment';
                        msg.innerText = 'Network error: ' + e.message;
                    }}
                }}
            </script>
        </body>
        </html>
    """)

@app.post("/api/v1/payments/mock-webhook")
async def mock_webhook_trigger(request: Request):
    """Trigger for mock payment simulation."""
    payload = await request.json()
    order_id = payload.get("order_id")
    payment_id = payload.get("payment_id")
    amount = float(payload.get("amount", 0.0))
    provider_payment_id = f"mock_{payment_id}"
    idempotency_key = f"mock_{order_id}_{payment_id}"

    result = await process_verified_payment(
        order_id=order_id,
        payment_id=payment_id,
        provider_payment_id=provider_payment_id,
        idempotency_key=idempotency_key,
        amount=amount
    )
    return {"status": "success", "result": result}
