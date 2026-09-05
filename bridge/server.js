const express = require('express');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcodeTerminal = require('qrcode-terminal');
const QRCode = require('qrcode');
const axios = require('axios');
const fs = require('fs');
const path = require('path');

const PORT = parseInt(process.env.BRIDGE_PORT || '3001', 10);
const FASTAPI_PORT = process.env.PORT || '8000';
const FASTAPI_WEBHOOK_URL = process.env.FASTAPI_WEBHOOK_URL || `http://127.0.0.1:${FASTAPI_PORT}/api/v1/whatsapp/webhook`;
const rawSessionPath = process.env.SESSION_DATA_PATH || path.join(__dirname, '..', 'data', 'session');
const SESSION_DATA_PATH = path.isAbsolute(rawSessionPath)
    ? rawSessionPath
    : path.resolve(__dirname, '..', rawSessionPath);

const rawMediaPath = process.env.MEDIA_STORAGE_PATH || path.join(__dirname, '..', 'data', 'media');
const MEDIA_STORAGE_PATH = path.isAbsolute(rawMediaPath)
    ? rawMediaPath
    : path.resolve(__dirname, '..', rawMediaPath);

console.log('[WhatsApp Bridge] BUILD MARKER: v3-lstat-fix-' + new Date().toISOString());

// Ensure storage directories exist
if (!fs.existsSync(SESSION_DATA_PATH)) {
    fs.mkdirSync(SESSION_DATA_PATH, { recursive: true });
}
if (!fs.existsSync(MEDIA_STORAGE_PATH)) {
    fs.mkdirSync(MEDIA_STORAGE_PATH, { recursive: true });
}

// Chromium refuses to launch if it finds singleton lock files left behind
// by a process that was killed without a clean shutdown (e.g. a redeploy
// that replaced the container mid-session). Since only one bridge instance
// ever runs against this volume, it's always safe to clear these on boot.
function removeStaleChromiumLocks(sessionPath) {
    const lockFiles = ['SingletonLock', 'SingletonCookie', 'SingletonSocket'];
    const searchDirs = [sessionPath];
    try {
        for (const entry of fs.readdirSync(sessionPath, { withFileTypes: true })) {
            if (entry.isDirectory()) {
                searchDirs.push(path.join(sessionPath, entry.name));
            }
        }
    } catch (err) {
        // sessionPath may not exist yet on first run — nothing to clean
    }
    for (const dir of searchDirs) {
        for (const lockFile of lockFiles) {
            const lockPath = path.join(dir, lockFile);
            try {
                // SingletonLock is a symlink pointing to "<hostname>-<pid>" of
                // whichever container last held it. Since Railway containers
                // get a fresh hostname on every deploy/restart, that target
                // never resolves again — and fs.existsSync() follows symlinks,
                // so it wrongly reports "doesn't exist" for a broken link and
                // skips it. lstatSync sees the symlink itself, broken or not.
                fs.lstatSync(lockPath);
                fs.unlinkSync(lockPath);
                console.log(`[WhatsApp Bridge] Removed stale Chromium lock: ${lockPath}`);
            } catch (err) {
                if (err.code !== 'ENOENT') {
                    console.warn(`[WhatsApp Bridge] Could not remove lock ${lockPath}:`, err.message);
                }
            }
        }
    }
}
removeStaleChromiumLocks(SESSION_DATA_PATH);

const app = express();
app.use(express.json({ limit: '50mb' }));

// Client state variables
let clientStatus = 'INITIALIZING'; // INITIALIZING | QR_READY | AUTHENTICATED | READY | DISCONNECTED | AUTH_FAILURE
let latestRawQr = null;
let latestQrDataUrl = null;
let botPhoneNumber = null;
const recentMessages = new Map(); // messageId -> raw message object for forwarding & quotes

// Determine Chromium path if available
const executablePath = process.env.PUPPETEER_EXECUTABLE_PATH ||
    (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' :
    (fs.existsSync('/usr/bin/chromium-browser') ? '/usr/bin/chromium-browser' : undefined));

console.log(`[WhatsApp Bridge] Initializing with Session Path: ${SESSION_DATA_PATH}`);
console.log(`[WhatsApp Bridge] Webhook URL: ${FASTAPI_WEBHOOK_URL}`);
if (executablePath) {
    console.log(`[WhatsApp Bridge] Using Chromium binary at: ${executablePath}`);
}

const client = new Client({
    authStrategy: new LocalAuth({
        dataPath: SESSION_DATA_PATH
    }),
    webVersionCache: {
        type: 'remote',
        remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/{version}.html',
        strict: false
    },
    puppeteer: {
        headless: true,
        executablePath: executablePath,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu',
            '--single-process'
        ]
    }
});

// Event Listeners
client.on('qr', async (qr) => {
    clientStatus = 'QR_READY';
    latestRawQr = qr;
    try {
        latestQrDataUrl = await QRCode.toDataURL(qr);
    } catch (err) {
        console.error('[WhatsApp Bridge] Error converting QR to data URL:', err);
    }

    console.log('\n================================================================');
    console.log('>>> SCAN THE WHATSAPP QR CODE BELOW WITH YOUR PERSONAL APP <<<');
    console.log('>>> Open WhatsApp -> Settings / Menu -> Linked Devices -> Link <<<');
    console.log('================================================================\n');
    qrcodeTerminal.generate(qr, { small: true });
    console.log('\n================================================================');
    console.log(`[WhatsApp Bridge] Web QR viewer is also available at: http://localhost:${PORT}/qr`);
    console.log('================================================================\n');
});

client.on('authenticated', () => {
    clientStatus = 'AUTHENTICATED';
    console.log('[WhatsApp Bridge] WhatsApp session successfully authenticated!');
});

client.on('auth_failure', (msg) => {
    clientStatus = 'AUTH_FAILURE';
    console.error('[WhatsApp Bridge] Authentication failure:', msg);
});

client.on('ready', () => {
    clientStatus = 'READY';
    latestRawQr = null;
    latestQrDataUrl = null;
    botPhoneNumber = client.info && client.info.wid ? client.info.wid.user : 'Unknown';
    console.log(`[WhatsApp Bridge] WhatsApp client is READY! Connected Phone: +${botPhoneNumber}`);
});

client.on('disconnected', async (reason) => {
    clientStatus = 'DISCONNECTED';
    console.warn(`[WhatsApp Bridge] WhatsApp disconnected. Reason: ${reason}`);
    console.log('[WhatsApp Bridge] Re-initializing client in 5 seconds...');
    setTimeout(() => {
        client.initialize().catch(err => console.error('[WhatsApp Bridge] Reconnect failed:', err));
    }, 5000);
});

/**
 * Asynchronously downloads media from a WhatsApp message with retry and timeout.
 * WhatsApp Web may take a few moments to decrypt and load media attachments from the CDN.
 * Calling downloadMedia() immediately upon message arrival often returns undefined.
 * Retrying with backoff allows sufficient time for the media stream to resolve.
 */
async function downloadMediaWithRetry(message, senderPhone, maxAttempts = 4, initialDelayMs = 800) {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            console.log(`[WhatsApp Bridge] Downloading media for message ${message.id && (message.id.id || message.id._serialized)} from ${senderPhone} (attempt ${attempt}/${maxAttempts})...`);

            // Warm up chat in memory before download (critical for @lid and background sessions)
            try {
                const chat = await message.getChat();
                if (chat && chat.syncHistory) {
                    await chat.syncHistory().catch(() => {});
                }
            } catch (wErr) {
                // Ignore warmup error and continue
            }

            let downloadedMedia = null;
            try {
                const downloadPromise = message.downloadMedia();
                const timeoutPromise = new Promise((_, reject) =>
                    setTimeout(() => reject(new Error('downloadMedia timed out after 15s')), 15000)
                );
                downloadedMedia = await Promise.race([downloadPromise, timeoutPromise]);
            } catch (err) {
                console.warn(`[WhatsApp Bridge] downloadMedia attempt ${attempt} error for ${senderPhone}:`, err.message);
            }

            if (downloadedMedia && downloadedMedia.data && downloadedMedia.data.length > 0) {
                console.log(`[WhatsApp Bridge] Media successfully downloaded on attempt ${attempt} for ${senderPhone} (mimetype: ${downloadedMedia.mimetype}, size: ${downloadedMedia.data.length} b64 chars)`);
                return downloadedMedia;
            }

            // 2. Fallback: Fetch the message directly from the chat model
            try {
                const chat = await message.getChat();
                if (chat) {
                    const recentMsgs = await chat.fetchMessages({ limit: 5 });
                    const match = recentMsgs.find(m => m.id && (m.id._serialized === (message.id && message.id._serialized)));
                    if (match && match.hasMedia) {
                        const fallbackMedia = await match.downloadMedia();
                        if (fallbackMedia && fallbackMedia.data && fallbackMedia.data.length > 0) {
                            console.log(`[WhatsApp Bridge] Media successfully downloaded via chat fetch fallback on attempt ${attempt}`);
                            return fallbackMedia;
                        }
                    }
                }
            } catch (fallbackErr) {
                console.warn(`[WhatsApp Bridge] Chat fetch fallback attempt ${attempt} error:`, fallbackErr.message);
            }

            // 3. Fallback: Query WhatsApp Web Store / DOM directly via Puppeteer
            if (client.pupPage) {
                try {
                    const serializedId = message.id && (message.id._serialized || String(message.id));
                    const rawId = message.id && (message.id.id || message.id._serialized || String(message.id));
                    const pupMedia = await client.pupPage.evaluate(async (sId, rId) => {
                        try {
                            if (!window.Store || !window.Store.Msg) return null;
                            const m = window.Store.Msg.get(sId) ||
                                (window.Store.Msg.models && window.Store.Msg.models.find(item =>
                                    item.id && (item.id._serialized === sId || item.id.id === rId)
                                ));
                            if (!m) return null;

                            // Check if decrypted renderableUrl blob is available
                            if (m.mediaData && m.mediaData.renderableUrl && window.WWebJS && window.WWebJS.readBlob) {
                                try {
                                    const b64 = await window.WWebJS.readBlob(m.mediaData.renderableUrl);
                                    if (b64) return { data: b64, mimetype: m.mimetype || 'image/jpeg', filename: m.filename || 'id_card.jpg' };
                                } catch (_) {}
                            }

                            // Check if preview data is available
                            if (m.mediaData && m.mediaData.preview) {
                                const p = m.mediaData.preview;
                                const b64 = typeof p === 'string' && p.startsWith('data:') ? p.split(',')[1] : (typeof p === 'string' ? p : null);
                                if (b64 && b64.length > 50) {
                                    return { data: b64, mimetype: m.mimetype || 'image/jpeg', filename: 'id_card.jpg' };
                                }
                            }

                            // Check if rendered in DOM img tag
                            const img = document.querySelector(`div[data-id*="${rId}"] img, div[data-id*="${sId}"] img`);
                            if (img && img.src && window.WWebJS && window.WWebJS.readBlob) {
                                try {
                                    const b64 = await window.WWebJS.readBlob(img.src);
                                    if (b64) return { data: b64, mimetype: 'image/jpeg', filename: 'id_card.jpg' };
                                } catch (_) {}
                            }
                        } catch (e) {
                            return null;
                        }
                        return null;
                    }, serializedId, rawId);

                    if (pupMedia && pupMedia.data) {
                        console.log(`[WhatsApp Bridge] Successfully recovered media via Puppeteer DOM/blob on attempt ${attempt}`);
                        return pupMedia;
                    }
                } catch (pupErr) {
                    console.warn(`[WhatsApp Bridge] Puppeteer media extraction error on attempt ${attempt}:`, pupErr.message);
                }
            }

            // 4. Fallback: Extract thumbnail/preview data embedded in message payload ONLY on final attempt
            if (attempt === maxAttempts) {
                try {
                    if (message._data) {
                        const d = message._data;
                        const candidates = [d.jpegThumbnail, d.thumbnail, d.preview, d.body];
                        for (const candidate of candidates) {
                            if (candidate) {
                                let b64Data = null;
                                if (Buffer.isBuffer(candidate)) {
                                    b64Data = candidate.toString('base64');
                                } else if (typeof candidate === 'string') {
                                    if (candidate.startsWith('data:')) {
                                        b64Data = candidate.split(',')[1];
                                    } else if (candidate.length > 50 && !candidate.includes(' ')) {
                                        b64Data = candidate;
                                    }
                                }

                                if (b64Data && b64Data.length > 50) {
                                    console.log(`[WhatsApp Bridge] Recovered media from message._data (${b64Data.length} chars)`);
                                    return {
                                        mimetype: d.mimetype || message.mimetype || 'image/jpeg',
                                        data: b64Data,
                                        filename: `id_card_${Date.now()}.jpg`
                                    };
                                }
                            }
                        }
                    }
                } catch (thumbErr) {
                    console.warn(`[WhatsApp Bridge] Thumbnail recovery attempt ${attempt} error:`, thumbErr.message);
                }
            }

            console.warn(`[WhatsApp Bridge] downloadMedia returned null/empty data on attempt ${attempt} for ${senderPhone}`);
        } catch (err) {
            console.warn(`[WhatsApp Bridge] downloadMedia attempt ${attempt} general error for ${senderPhone}:`, err.message);
        }

        if (attempt < maxAttempts) {
            const waitTime = initialDelayMs * attempt;
            console.log(`[WhatsApp Bridge] Waiting ${waitTime}ms before retry attempt ${attempt + 1}...`);
            await new Promise(r => setTimeout(r, waitTime));
        }
    }
    return null;
}

/**
 * Resolves the real phone number for an incoming sender.
 * WhatsApp frequently sends messages from privacy-preserving @lid (Linked Identity)
 * JIDs (e.g. 162947334668337@lid). Stripping '@lid' leaves an internal ID,
 * not the contact's actual phone number. This resolves @lid to phone number (pn).
 */
async function resolveSenderPhone(message, senderJid) {
    let rawDigits = senderJid.replace(/@c\.us$/, '').replace(/@lid$/, '').replace(/@g\.us$/, '');

    if (!senderJid.endsWith('@lid')) {
        return rawDigits;
    }

    // 1. Try whatsapp-web.js built-in getContactLidAndPhone
    try {
        if (typeof client.getContactLidAndPhone === 'function') {
            const mappings = await client.getContactLidAndPhone([senderJid]);
            if (mappings && Array.isArray(mappings) && mappings.length > 0) {
                const item = mappings.find(m => m && (m.lid === senderJid || m.lid === rawDigits)) || mappings[0];
                if (item && item.pn) {
                    const pnDigits = item.pn.replace(/@c\.us$/, '').replace(/[^0-9]/g, '');
                    if (pnDigits) {
                        console.log(`[WhatsApp Bridge] Resolved LID ${senderJid} -> Phone ${pnDigits} via getContactLidAndPhone`);
                        return pnDigits;
                    }
                }
            }
        }
    } catch (err) {
        console.warn(`[WhatsApp Bridge] getContactLidAndPhone lookup failed for ${senderJid}:`, err.message);
    }

    // 2. Try message.getContact()
    try {
        const contact = await message.getContact();
        if (contact) {
            if (contact.number && !contact.number.includes('@')) {
                const cNum = contact.number.replace(/[^0-9]/g, '');
                if (cNum && cNum !== rawDigits) {
                    console.log(`[WhatsApp Bridge] Resolved LID ${senderJid} -> Phone ${cNum} via contact.number`);
                    return cNum;
                }
            }
            if (contact.id && contact.id.server === 'c.us' && contact.id.user) {
                const cNum = contact.id.user.replace(/[^0-9]/g, '');
                if (cNum && cNum !== rawDigits) {
                    console.log(`[WhatsApp Bridge] Resolved LID ${senderJid} -> Phone ${cNum} via contact.id.user`);
                    return cNum;
                }
            }
        }
    } catch (err) {
        console.warn(`[WhatsApp Bridge] getContact lookup failed for ${senderJid}:`, err.message);
    }

    // 3. Fallback: Query WhatsApp Web internal Store via Puppeteer
    if (client.pupPage) {
        try {
            const storePn = await client.pupPage.evaluate(async (lid) => {
                try {
                    if (!window.Store) return null;

                    // Try LidUtils
                    if (window.Store.LidUtils && typeof window.Store.LidUtils.getPhoneNumber === 'function') {
                        const wid = window.Store.WidFactory ? window.Store.WidFactory.createWid(lid) : lid;
                        const pnWid = await window.Store.LidUtils.getPhoneNumber(wid);
                        if (pnWid) {
                            const user = pnWid.user || (typeof pnWid === 'string' ? pnWid.split('@')[0] : null);
                            if (user) return user;
                        }
                    }

                    // Try Contact store
                    if (window.Store.Contact) {
                        const c = window.Store.Contact.get(lid);
                        if (c) {
                            if (c.phoneNumber) return c.phoneNumber;
                            if (c.id && c.id.server === 'c.us') return c.id.user;
                            if (c.userid && !c.userid.includes('@')) return c.userid;
                        }
                    }

                    // Try WWebJS helper
                    if (window.WWebJS && typeof window.WWebJS.getContactLidAndPhone === 'function') {
                        const res = await window.WWebJS.getContactLidAndPhone([lid]);
                        if (res && res[0] && res[0].pn) {
                            return res[0].pn.split('@')[0];
                        }
                    }
                } catch (_) {
                    return null;
                }
                return null;
            }, senderJid);

            if (storePn) {
                const pnDigits = String(storePn).replace(/[^0-9]/g, '');
                if (pnDigits && pnDigits !== rawDigits) {
                    console.log(`[WhatsApp Bridge] Resolved LID ${senderJid} -> Phone ${pnDigits} via Puppeteer Store`);
                    return pnDigits;
                }
            }
        } catch (err) {
            console.warn(`[WhatsApp Bridge] Puppeteer Store LID lookup failed for ${senderJid}:`, err.message);
        }
    }

    return rawDigits;
}

client.on('message', async (message) => {
    try {
        // Ignore status broadcasts and messages sent by the bot itself
        if (message.isStatus || message.fromMe) {
            return;
        }

        const senderJid = message.from; // raw JID — may be @c.us, @lid, or @g.us
        const senderPhone = await resolveSenderPhone(message, senderJid);
        let mediaInfo = null;

        // Check if message has media either via hasMedia flag or media-related message types
        const hasMediaFlag = Boolean(
            message.hasMedia ||
            ['image', 'document', 'video', 'audio', 'ptt', 'sticker'].includes(message.type)
        );

        if (hasMediaFlag) {
            try {
                const downloadedMedia = await downloadMediaWithRetry(message, senderPhone);
                if (downloadedMedia) {
                    let ext = 'bin';
                    if (downloadedMedia.mimetype) {
                        const rawExt = downloadedMedia.mimetype.split('/')[1]?.split(';')[0]?.toLowerCase();
                        if (rawExt) {
                            if (rawExt === 'jpeg' || rawExt === 'jpg') ext = 'jpg';
                            else if (rawExt === 'png') ext = 'png';
                            else if (rawExt === 'pdf') ext = 'pdf';
                            else if (rawExt === 'webp') ext = 'webp';
                            else ext = rawExt.replace(/[^a-z0-9]/g, '') || 'bin';
                        }
                    }
                    if (downloadedMedia.filename && downloadedMedia.filename.includes('.')) {
                        const origExt = downloadedMedia.filename.split('.').pop()?.toLowerCase()?.replace(/[^a-z0-9]/g, '');
                        if (origExt) ext = origExt;
                    }

                    const filename = `media_${Date.now()}_${Math.random().toString(36).substring(2, 8)}.${ext}`;
                    const absoluteFilePath = path.resolve(MEDIA_STORAGE_PATH, filename);

                    // Ensure storage directory exists
                    if (!fs.existsSync(MEDIA_STORAGE_PATH)) {
                        fs.mkdirSync(MEDIA_STORAGE_PATH, { recursive: true });
                    }

                    // Synchronously write buffer and flush
                    const buffer = Buffer.from(downloadedMedia.data, 'base64');
                    fs.writeFileSync(absoluteFilePath, buffer);

                    mediaInfo = {
                        filename: filename,
                        filePath: absoluteFilePath,
                        mimetype: downloadedMedia.mimetype || 'application/octet-stream',
                        filesize: buffer.length,
                        type: message.type || 'unknown'
                    };
                    console.log(`[WhatsApp Bridge] Saved media to ${absoluteFilePath} (${buffer.length} bytes, mimetype=${mediaInfo.mimetype})`);
                } else {
                    console.error(`[WhatsApp Bridge] Failed to download media from ${senderPhone} after all retry attempts.`);
                }
            } catch (mediaErr) {
                console.error(`[WhatsApp Bridge] Exception during media download/saving for ${senderPhone}:`, mediaErr);
            }
        }

        const msgRawId = message.id && (message.id.id || message.id._serialized || String(message.id));
        if (msgRawId) {
            recentMessages.set(msgRawId, message);
        }
        if (message.id && message.id._serialized) {
            recentMessages.set(message.id._serialized, message);
        }
        if (recentMessages.size > 500) {
            const oldestKey = recentMessages.keys().next().value;
            recentMessages.delete(oldestKey);
        }

        // Extract quoted message details (when user or admin swipes to reply on WhatsApp)
        let quotedInfo = null;
        if (message.hasQuotedMsg) {
            try {
                const quotedMsg = await message.getQuotedMessage();
                if (quotedMsg) {
                    quotedInfo = {
                        message_id: quotedMsg.id && (quotedMsg.id.id || quotedMsg.id._serialized || String(quotedMsg.id)),
                        body: quotedMsg.body || '',
                        caption: quotedMsg.caption || '',
                        hasMedia: Boolean(quotedMsg.hasMedia),
                        type: quotedMsg.type || 'chat',
                        from: quotedMsg.from
                    };
                }
            } catch (qErr) {
                console.warn(`[WhatsApp Bridge] Failed to fetch quoted message for ${msgRawId}:`, qErr.message);
            }
        }

        const payload = {
            message_id: msgRawId,
            from: senderJid,
            sender_jid: senderJid,
            sender_phone: senderPhone,
            body: message.body || '',
            timestamp: message.timestamp,
            has_media: Boolean(mediaInfo && mediaInfo.filePath),
            raw_has_media: hasMediaFlag,
            media: mediaInfo,
            has_quoted_msg: Boolean(quotedInfo),
            quoted_message: quotedInfo
        };

        // Forward to FastAPI webhook
        axios.post(FASTAPI_WEBHOOK_URL, payload, { timeout: 15000 })
            .then(res => {
                console.log(`[WhatsApp Bridge] Forwarded message ${payload.message_id} to FastAPI (status: ${res.status}, has_media=${payload.has_media})`);
            })
            .catch(err => {
                console.error(`[WhatsApp Bridge] Failed to forward message to FastAPI webhook (${FASTAPI_WEBHOOK_URL}):`, err.message);
            });
    } catch (err) {
        console.error('[WhatsApp Bridge] Error processing incoming message:', err);
    }
});

// REST Endpoints for FastAPI & Monitoring
app.get('/health', (req, res) => {
    res.json({
        ok: true,
        status: clientStatus,
        botPhoneNumber: botPhoneNumber,
        timestamp: new Date().toISOString()
    });
});

app.get('/status', (req, res) => {
    res.json({
        status: clientStatus,
        botPhoneNumber: botPhoneNumber,
        hasQr: !!latestQrDataUrl
    });
});

app.get('/qr', (req, res) => {
    if (clientStatus === 'READY') {
        return res.send(`
            <html>
                <body style="font-family:sans-serif; text-align:center; padding:50px; background:#f0f2f5;">
                    <h2 style="color:#128c7e;">WhatsApp Bot is Connected!</h2>
                    <p>Phone: +${botPhoneNumber || 'Active'}</p>
                    <p>Session is saved in persistent storage.</p>
                </body>
            </html>
        `);
    }
    if (!latestQrDataUrl) {
        return res.send(`
            <html>
                <body style="font-family:sans-serif; text-align:center; padding:50px; background:#f0f2f5;">
                    <h2>Initializing WhatsApp Session...</h2>
                    <p>Status: <strong>${clientStatus}</strong></p>
                    <p>Please refresh this page in a few seconds once the QR code is generated.</p>
                    <script>setTimeout(() => location.reload(), 3000);</script>
                </body>
            </html>
        `);
    }

    res.send(`
        <!DOCTYPE html>
        <html>
        <head>
            <title>WhatsApp Web QR Login</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #ece5dd; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
                .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); text-align: center; max-width: 400px; width: 90%; }
                h2 { color: #075e54; margin-top: 0; }
                img { border: 4px solid #128c7e; border-radius: 8px; margin: 15px 0; max-width: 260px; }
                ol { text-align: left; font-size: 14px; color: #4a4a4a; line-height: 1.6; }
                .status-badge { display: inline-block; background: #e7f8e9; color: #1b5e20; padding: 4px 10px; border-radius: 20px; font-weight: 600; font-size: 12px; }
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Scan WhatsApp QR</h2>
                <span class="status-badge">${clientStatus}</span>
                <div>
                    <img src="${latestQrDataUrl}" alt="WhatsApp QR Code" />
                </div>
                <ol>
                    <li>Open WhatsApp on your phone</li>
                    <li>Tap <b>Menu</b> or <b>Settings</b> & select <b>Linked Devices</b></li>
                    <li>Tap <b>Link a Device</b> and point your phone to this screen</li>
                </ol>
                <script>
                    setInterval(async () => {
                        try {
                            const res = await fetch('/status');
                            const data = await res.json();
                            if (data.status === 'READY') {
                                location.reload();
                            }
                        } catch(e) {}
                    }, 4000);
                </script>
            </div>
        </body>
        </html>
    `);
});

app.get('/qr-data', (req, res) => {
    res.json({
        status: clientStatus,
        raw: latestRawQr,
        dataUrl: latestQrDataUrl
    });
});

// WhatsApp has been migrating some contacts to privacy-preserving @lid
// addressing instead of the classic @c.us phone-number JID. Sending directly
// to a guessed @c.us id for such a contact throws "No LID for user". Forcing
// a resolution via getNumberId() makes the client look up the contact's
// current WhatsApp id (whichever form it actually uses) before sending.
async function resolveChatId(rawTo) {
    const toStr = rawTo.toString();
    if (toStr.includes('@')) {
        return toStr; // already a full JID (e.g. stored @lid) — trust it
    }
    const digits = toStr.replace(/[^0-9]/g, '');
    try {
        const numberId = await client.getNumberId(digits);
        if (numberId && numberId._serialized) {
            return numberId._serialized;
        }
    } catch (err) {
        console.warn(`[WhatsApp Bridge] getNumberId lookup failed for ${digits}, falling back to @c.us:`, err.message);
    }
    return `${digits}@c.us`;
}

app.post('/send-message', async (req, res) => {
    const { to, message } = req.body;
    if (!to || !message) {
        return res.status(400).json({ error: 'Fields "to" and "message" are required.' });
    }

    if (clientStatus !== 'READY') {
        return res.status(503).json({ error: `WhatsApp client is not ready. Current status: ${clientStatus}` });
    }

    try {
        const chatId = await resolveChatId(to);

        // Optional natural typing simulation delay
        const delayMs = Math.floor(Math.random() * 500) + 300;
        await new Promise(r => setTimeout(r, delayMs));

        let result;
        try {
            result = await client.sendMessage(chatId, message);
        } catch (sendErr) {
            console.warn(`[WhatsApp Bridge] Primary sendMessage to ${chatId} failed (${sendErr.message}), trying fallback...`);
            if (chatId.includes('@lid')) {
                const fallbackChatId = chatId.replace(/@lid$/, '@c.us');
                result = await client.sendMessage(fallbackChatId, message);
            } else {
                throw sendErr;
            }
        }

        const messageId = (result && result.id && (result.id.id || result.id._serialized || result.id))
            || (result && (result._serialized || result.id))
            || 'MSG_' + Date.now();
        console.log(`[WhatsApp Bridge] Message sent to ${chatId}: messageId=${messageId}`);
        res.json({ success: true, messageId: messageId });
    } catch (err) {
        console.error(`[WhatsApp Bridge] Failed to send message to ${to}:`, err);
        res.status(500).json({ error: err.message });
    }
});

app.post('/send-media', async (req, res) => {
    const { to, filePath, caption, mimetype } = req.body;
    if (!to || !filePath) {
        return res.status(400).json({ error: 'Fields "to" and "filePath" are required.' });
    }

    if (clientStatus !== 'READY') {
        return res.status(503).json({ error: `WhatsApp client is not ready. Current status: ${clientStatus}` });
    }

    try {
        const resolvedPath = path.isAbsolute(filePath)
            ? filePath
            : path.resolve(MEDIA_STORAGE_PATH, path.basename(filePath));

        if (!fs.existsSync(resolvedPath)) {
            console.error(`[WhatsApp Bridge] File not found at path: ${filePath} (resolved: ${resolvedPath})`);
            return res.status(404).json({ error: `File not found at path: ${filePath}` });
        }

        let media;
        try {
            media = MessageMedia.fromFilePath(resolvedPath);
        } catch (mediaReadErr) {
            console.warn(`[WhatsApp Bridge] MessageMedia.fromFilePath failed, creating manually: ${mediaReadErr.message}`);
            const fileData = fs.readFileSync(resolvedPath).toString('base64');
            const fileName = path.basename(resolvedPath);
            media = new MessageMedia(mimetype || 'application/octet-stream', fileData, fileName);
        }

        if (mimetype) {
            media.mimetype = mimetype;
        }

        const chatId = await resolveChatId(to);

        let result;
        try {
            result = await client.sendMessage(chatId, media, { caption: caption || '' });
        } catch (sendErr) {
            console.warn(`[WhatsApp Bridge] Primary sendMedia to ${chatId} failed (${sendErr.message}), trying fallback...`);
            if (chatId.includes('@lid')) {
                const fallbackChatId = chatId.replace(/@lid$/, '@c.us');
                result = await client.sendMessage(fallbackChatId, media, { caption: caption || '' });
            } else {
                throw sendErr;
            }
        }

        const messageId = (result && result.id && (result.id.id || result.id._serialized || result.id))
            || (result && (result._serialized || result.id))
            || 'MEDIA_' + Date.now();
        console.log(`[WhatsApp Bridge] Media message sent to ${chatId}: messageId=${messageId}`);
        res.json({ success: true, messageId: messageId });
    } catch (err) {
        console.error(`[WhatsApp Bridge] Failed to send media to ${to}:`, err);
        res.status(500).json({ error: err.message });
    }
});

app.post('/forward-message', async (req, res) => {
    const { message_id, to } = req.body;
    if (!message_id || !to) {
        return res.status(400).json({ error: 'Fields "message_id" and "to" are required.' });
    }

    if (clientStatus !== 'READY') {
        return res.status(503).json({ error: `WhatsApp client is not ready. Current status: ${clientStatus}` });
    }

    try {
        const chatId = await resolveChatId(to);
        let msg = recentMessages.get(message_id);
        if (!msg) {
            try {
                msg = await client.getMessageById(message_id);
            } catch (fetchErr) {
                console.warn(`[WhatsApp Bridge] getMessageById failed for ${message_id}:`, fetchErr.message);
            }
        }

        if (!msg) {
            return res.status(404).json({ error: `Message ${message_id} not found in bridge cache.` });
        }

        const result = await msg.forward(chatId);
        const forwardedId = (result && result.id && (result.id.id || result.id._serialized || result.id))
            || (result && (result._serialized || result.id))
            || 'FWD_' + Date.now();
        console.log(`[WhatsApp Bridge] Successfully forwarded original message ${message_id} to ${chatId}: forwardedId=${forwardedId}`);
        res.json({ success: true, messageId: forwardedId });
    } catch (err) {
        console.error(`[WhatsApp Bridge] Failed to forward message ${message_id} to ${to}:`, err);
        res.status(500).json({ error: err.message });
    }
});

// Start Express server
app.listen(PORT, '0.0.0.0', () => {
    console.log(`[WhatsApp Bridge] HTTP Server running on http://0.0.0.0:${PORT}`);
    console.log('[WhatsApp Bridge] Initializing WhatsApp Web Client...');

    const startClient = (attempt = 1) => {
        removeStaleChromiumLocks(SESSION_DATA_PATH);
        client.initialize().catch(err => {
            console.error(`[WhatsApp Bridge] Failed to initialize WhatsApp Client (attempt ${attempt}):`, err.message);
            if (attempt < 5) {
                const delay = Math.min(5000 * attempt, 20000);
                console.log(`[WhatsApp Bridge] Retrying initialization in ${delay / 1000}s...`);
                setTimeout(() => startClient(attempt + 1), delay);
            } else {
                console.error('[WhatsApp Bridge] Giving up after 5 attempts. Manual redeploy/restart needed.');
            }
        });
    };
    startClient();
});
