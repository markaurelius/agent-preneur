'use strict';

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const express = require('express');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const fs = require('fs');

const AUTH_DIR  = process.env.AUTH_DIR  || '/home/agent/auth';
const PORT      = parseInt(process.env.PORT || '3000', 10);
const STATUS_FILE = '/tmp/agent_status.txt';
const SIGNAL_FILE = '/tmp/agent_signal.txt';

const logger = pino({ level: 'silent' });

const app = express();
app.use(express.json());

let sock    = null;
let isReady = false;
const inbox = [];  // free-form messages for Claude to read

function authorizedJid() {
  const to = process.env.WHATSAPP_TO;
  return to ? to.replace(/[^0-9]/g, '') + '@s.whatsapp.net' : null;
}

function toJid(phone) {
  return phone.replace(/[^0-9]/g, '') + '@s.whatsapp.net';
}

function readStatus() {
  try { return fs.readFileSync(STATUS_FILE, 'utf8').trim(); }
  catch { return 'Agent not currently running (no status file found).'; }
}

function writeSignal(signal) {
  fs.writeFileSync(SIGNAL_FILE, signal, 'utf8');
}

// Route an incoming message — returns reply text, or null to put it in the inbox
function route(text) {
  const cmd = text.trim().toLowerCase();

  if (cmd === 'status')   return `*Status*\n${readStatus()}`;
  if (cmd === 'help')     return (
    '*Commands*\n' +
    '• `status` — current iteration and Brier score\n' +
    '• `continue` — resume past the 5-iteration checkpoint\n' +
    '• `stop` — stop after the current iteration\n' +
    '• `stop now` — stop immediately\n' +
    '• `skip` — skip current idea, try the next one\n' +
    '• Anything else — free-form instruction passed to the agent'
  );

  if (cmd === 'continue')  { writeSignal('CONTINUE');  return '✓ Signal sent: agent will continue past checkpoint.'; }
  if (cmd === 'stop')      { writeSignal('STOP');       return '✓ Signal sent: agent will stop after this iteration.'; }
  if (cmd === 'stop now')  { writeSignal('STOP_NOW');   return '✓ Signal sent: agent will stop immediately.'; }
  if (cmd === 'skip')      { writeSignal('SKIP');       return '✓ Signal sent: agent will skip the current idea.'; }

  return null;  // goes to inbox
}

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    browser: ['Stock Agent', 'Chrome', '1.0.0'],
    markOnlineOnConnect: false,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log('\n[wa] Scan this QR code with WhatsApp → Linked Devices → Link a Device:\n');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'close') {
      isReady = false;
      const code = new Boom(lastDisconnect?.error)?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        console.error('[wa] Logged out. Delete the auth volume and re-run make whatsapp-setup.');
        process.exit(1);
      }
      const delay = code === DisconnectReason.restartRequired ? 0 : 3000;
      console.log(`[wa] Disconnected (${code}) — reconnecting in ${delay}ms`);
      setTimeout(connect, delay);
    } else if (connection === 'open') {
      isReady = true;
      console.log('[wa] Connected ✓');
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    const authJid = authorizedJid();

    for (const msg of messages) {
      const sender = msg.key.remoteJid;

      // Accept self-messages (user → Saved Messages, fromMe:true, remoteJid = own number)
      // Accept messages from the authorized number (fromMe:false, remoteJid = their number)
      // Reject everything else (agent's outgoing messages to other people, strangers)
      const isSelfCommand = msg.key.fromMe  && authJid && sender === authJid;
      const isFromUser    = !msg.key.fromMe && authJid && sender === authJid;
      if (!isSelfCommand && !isFromUser) continue;

      const text =
        msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        '';
      if (!text.trim()) continue;

      console.log(`[wa] Received: "${text.trim()}"`);
      const reply = route(text);

      if (reply) {
        // Known command — reply immediately
        if (isReady) {
          await sock.sendMessage(sender, { text: reply }).catch(() => {});
        }
      } else {
        // Free-form — queue for Claude
        inbox.push({ text: text.trim(), ts: Date.now() });
        if (isReady) {
          await sock
            .sendMessage(sender, { text: `✓ Queued for agent: "${text.trim()}"` })
            .catch(() => {});
        }
      }
    }
  });
}

// ── HTTP API ────────────────────────────────────────────────────────────────

// POST /send  { "message": "text" }
app.post('/send', async (req, res) => {
  const { message } = req.body || {};
  if (!message) return res.status(400).json({ error: 'message is required' });

  const to = process.env.WHATSAPP_TO;
  if (!to)       return res.status(500).json({ error: 'WHATSAPP_TO not set' });
  if (!isReady)  return res.status(503).json({ error: 'WhatsApp not connected' });

  try {
    await sock.sendMessage(toJid(to), { text: message });
    res.json({ ok: true });
  } catch (err) {
    console.error('[wa] Send error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /inbox — pending free-form messages for Claude; clears the queue
app.get('/inbox', (_req, res) => {
  res.json({ messages: inbox.splice(0) });
});

// POST /status  { "text": "one-line status string" }  — called by Claude after each iteration
app.post('/status', (req, res) => {
  const { text } = req.body || {};
  if (!text) return res.status(400).json({ error: 'text is required' });
  fs.writeFileSync(STATUS_FILE, text, 'utf8');
  res.json({ ok: true });
});

// GET /signal — returns pending signal (CONTINUE/STOP/STOP_NOW/SKIP) and clears it
app.get('/signal', (_req, res) => {
  try {
    const signal = fs.readFileSync(SIGNAL_FILE, 'utf8').trim();
    fs.unlinkSync(SIGNAL_FILE);
    res.json({ signal });
  } catch {
    res.json({ signal: null });
  }
});

// GET /health
app.get('/health', (_req, res) => res.json({ connected: isReady }));

app.listen(PORT, () => console.log(`[wa] HTTP bridge on :${PORT}`));

connect().catch((err) => {
  console.error('[wa] Fatal:', err);
  process.exit(1);
});
