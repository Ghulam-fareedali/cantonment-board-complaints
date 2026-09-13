// pairctl - the pairing sidecar for one Hermes agent.
//
// Why this exists. The Hermes gateway starts the WhatsApp bridge, waits about
// thirteen seconds for it to report a paired connection, and tears it down when
// it does not. That is sensible for a bot that is already paired and useless for
// pairing one: there is nothing alive for a client to scan. The gateway also
// exposes no API to ask it for a pairing session.
//
// So pairing gets its own process, owned by us. This sidecar runs beside one
// agent, mounts that agent's data volume and nothing else, and can do exactly
// three things: start a pairing bridge, report its state, stop it. It holds no
// Docker privileges and it can reach no other tenant's data.
//
// The moment the number is paired the sidecar stops the pairing bridge, because
// the credentials are now on the volume and the gateway's own bridge is the one
// that should be talking to customers.

import http from 'http';
import { spawn } from 'child_process';

const PORT        = parseInt(process.env.PAIRCTL_PORT || '3010', 10);
const BRIDGE_PORT = parseInt(process.env.PAIRCTL_BRIDGE_PORT || '3011', 10);
const BRIDGE_JS   = process.env.PAIRCTL_BRIDGE_JS || '/opt/data/scripts/whatsapp-bridge/bridge.js';
const SESSION_DIR = process.env.PAIRCTL_SESSION_DIR || '/opt/data/whatsapp/session';
const KEY         = process.env.HERMES_INTERNAL_KEY || '';
const AGENT       = process.env.PAIRCTL_AGENT || 'unknown';

// A pairing session that nobody completes should not sit there forever offering
// a live QR for someone's business number.
const MAX_PAIRING_MS = 10 * 60 * 1000;

let child = null;
let startedAt = null;

function log(...a) { console.log(new Date().toISOString(), '[pairctl]', ...a); }

function startBridge() {
  if (child) return { started: false, already: true };
  child = spawn(process.execPath, [BRIDGE_JS, '--port', String(BRIDGE_PORT), '--session', SESSION_DIR], {
    cwd: BRIDGE_JS.replace(/\/[^/]+$/, ''),
    stdio: ['ignore', 'pipe', 'pipe']
  });
  startedAt = Date.now();
  child.stdout.on('data', d => process.stdout.write(d));
  child.stderr.on('data', d => process.stderr.write(d));
  child.on('exit', (code) => { log('pairing bridge exited', code); child = null; startedAt = null; });
  log('pairing bridge started on', BRIDGE_PORT, 'session', SESSION_DIR);
  return { started: true };
}

function stopBridge(why) {
  if (!child) return { stopped: false };
  log('stopping pairing bridge:', why);
  try { child.kill('SIGTERM'); } catch (e) {}
  child = null; startedAt = null;
  return { stopped: true };
}

function bridgeSession() {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port: BRIDGE_PORT, path: '/session', timeout: 3000 }, (res) => {
      let body = '';
      res.on('data', c => body += c);
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
  });
}

function send(res, code, obj) {
  const b = JSON.stringify(obj);
  res.writeHead(code, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(b) });
  res.end(b);
}

const server = http.createServer(async (req, res) => {
  // No key, no service. A pairing code and a logout are both things you only
  // want the platform doing, and this port is reachable by everything on the
  // Docker network.
  if (!KEY) return send(res, 503, { error: 'HERMES_INTERNAL_KEY is not set; refusing to serve' });
  if ((req.headers['x-internal-key'] || '') !== KEY) return send(res, 401, { error: 'bad internal key' });

  const url = (req.url || '').split('?')[0];

  if (req.method === 'POST' && url === '/pair/start') {
    const r = startBridge();
    return send(res, 200, { agent: AGENT, ...r, bridge_port: BRIDGE_PORT });
  }

  if (req.method === 'POST' && url === '/pair/stop') {
    return send(res, 200, { agent: AGENT, ...stopBridge('asked to') });
  }

  if (req.method === 'GET' && url === '/pair/status') {
    if (!child) return send(res, 200, { agent: AGENT, running: false, state: 'idle', qr: null });
    if (Date.now() - startedAt > MAX_PAIRING_MS) {
      stopBridge('pairing window expired');
      return send(res, 200, { agent: AGENT, running: false, state: 'expired', qr: null });
    }
    const s = await bridgeSession();
    if (!s) return send(res, 200, { agent: AGENT, running: true, state: 'starting', qr: null });
    if (s.connected) {
      // Credentials are on the volume now. Hand the number back to the gateway.
      stopBridge('paired');
      return send(res, 200, { agent: AGENT, running: false, state: 'connected',
                              number: s.number, name: s.name, qr: null });
    }
    return send(res, 200, { agent: AGENT, running: true, ...s });
  }

  if (req.method === 'GET' && url === '/health') {
    return send(res, 200, { ok: true, agent: AGENT, pairing: !!child });
  }

  send(res, 404, { error: 'not found' });
});

server.listen(PORT, '0.0.0.0', () => log('listening on', PORT, 'for agent', AGENT));
process.on('SIGTERM', () => { stopBridge('shutting down'); process.exit(0); });
