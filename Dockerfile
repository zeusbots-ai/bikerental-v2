# Multi-runtime Dockerfile (Python 3.11 + Node.js 22 + Chromium)
FROM python:3.11-slim-bookworm

# Environment flags for Puppeteer and Python
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    SESSION_DATA_PATH=/data/session \
    MEDIA_STORAGE_PATH=/data/media \
    PORT=8000 \
    BRIDGE_PORT=3001

# Install system utilities, Chromium browser, and dependencies for Puppeteer
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    chromium \
    fonts-liberation \
    libayatana-appindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22.x
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Node.js dependencies for bridge
COPY bridge/package.json bridge/package-lock.json* ./bridge/
WORKDIR /app/bridge
RUN npm install --omit=dev
WORKDIR /app

# Copy application source code
COPY app/ ./app/
COPY bridge/ ./bridge/
COPY entrypoint.sh .

# Create persistent storage directories
RUN mkdir -p /data/session /data/media && chmod -R 777 /data
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

CMD ["/app/entrypoint.sh"]
