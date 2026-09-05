#!/bin/bash
set -e

# Railway provides PORT dynamically
APP_PORT=${PORT:-8000}
BRIDGE_PORT=${BRIDGE_PORT:-3001}

echo "================================================================"
echo "🚀 Starting CUAP Hostel Bike Rental Application"
echo "   FastAPI Port : $APP_PORT"
echo "   Bridge Port  : $BRIDGE_PORT"
echo "   Session Path : ${SESSION_DATA_PATH:-./data/session}"
echo "   Media Path   : ${MEDIA_STORAGE_PATH:-./data/media}"
echo "================================================================"

# Ensure directories exist
mkdir -p "${SESSION_DATA_PATH:-/data/session}"
mkdir -p "${MEDIA_STORAGE_PATH:-/data/media}"

# Trap termination signals to stop background services
cleanup() {
    echo "Caught shutdown signal. Stopping services..."
    if [ -n "$FASTAPI_PID" ]; then
        kill -TERM "$FASTAPI_PID" 2>/dev/null || true
    fi
    if [ -n "$BRIDGE_PID" ]; then
        kill -TERM "$BRIDGE_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM

# Start Python FastAPI server in the background FIRST. The WhatsApp bridge
# forwards every incoming message to FastAPI's webhook immediately after it
# authenticates, so FastAPI must already be accepting requests — otherwise
# any message that arrives in the first few seconds after boot is dropped
# with ECONNREFUSED and never retried.
echo "[Init] Launching FastAPI on 0.0.0.0:$APP_PORT..."
uvicorn app.main:app --host 0.0.0.0 --port "$APP_PORT" &
FASTAPI_PID=$!

# Wait for FastAPI to actually accept requests (not just "process started")
# by polling its own /health endpoint, instead of guessing a fixed sleep.
echo "[Init] Waiting for FastAPI to become healthy..."
MAX_WAIT=60
WAITED=0
until curl -sf "http://127.0.0.1:${APP_PORT}/health" > /dev/null 2>&1; do
    if ! kill -0 "$FASTAPI_PID" 2>/dev/null; then
        echo "[Init] FastAPI process exited unexpectedly during startup."
        exit 1
    fi
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo "[Init] FastAPI did not become healthy within ${MAX_WAIT}s, starting bridge anyway."
        break
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done
echo "[Init] FastAPI is up after ${WAITED}s."

# Now start the Node.js WhatsApp Web bridge
echo "[Init] Launching WhatsApp Web Bridge on port $BRIDGE_PORT..."
export FASTAPI_WEBHOOK_URL="http://127.0.0.1:${APP_PORT}/api/v1/whatsapp/webhook"
cd /app/bridge
FASTAPI_WEBHOOK_URL=$FASTAPI_WEBHOOK_URL BRIDGE_PORT=$BRIDGE_PORT node server.js &
BRIDGE_PID=$!
cd /app

# Wait on both background processes; exit if either dies
wait -n "$FASTAPI_PID" "$BRIDGE_PID"
