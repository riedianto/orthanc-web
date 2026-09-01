'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const net = require('net');
const httpProxy = require('http-proxy');

const PROXY_PORT = parseInt(process.env.PROXY_PORT || '8090', 10);
const UPSTREAM = process.env.UPSTREAM || 'http://orthanc:8042';
const ORTHANC_USER = process.env.ORTHANC_USER || 'orthanc';
const ORTHANC_PASSWORD = process.env.ORTHANC_PASSWORD || 'orthanc';

const WORKLISTS_DIR = fs.existsSync('/var/lib/orthanc/worklists')
  ? '/var/lib/orthanc/worklists'
  : path.join(__dirname, '..', 'worklists');

if (!fs.existsSync(WORKLISTS_DIR)) {
  try { fs.mkdirSync(WORKLISTS_DIR, { recursive: true }); } catch (e) {}
}

const SETTINGS_FILE = path.join(WORKLISTS_DIR, 'system_settings.json');
const BLACKLIST_FILE = path.join(WORKLISTS_DIR, 'completed_worklist.json');

const SESSION_COOKIE = 'orthanc_session';
const SESSION_TTL_MS = 12 * 60 * 60 * 1000;

const sessions = new Map();

function base64(str) {
  return Buffer.from(str, 'utf8').toString('base64');
}

function makeBasicAuth(user, pass) {
  return 'Basic ' + base64(`${user}:${pass}`);
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => {
      data += c;
      if (data.length > 2e6) {
        reject(new Error('Payload too large'));
        req.destroy();
      }
    });
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

function validateCredentials(user, pass) {
  const headers = { 'Authorization': makeBasicAuth(user, pass) };
  return new Promise((resolve) => {
    const req = http.request(UPSTREAM, {
      method: 'GET',
      path: '/system',
      headers,
      timeout: 10000,
    }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

function createSession(creds) {
  const token = crypto.randomBytes(32).toString('hex');
  sessions.set(token, { creds, expires: Date.now() + SESSION_TTL_MS });
  return token;
}

function getSession(req) {
  const header = req.headers.cookie || '';
  const match = header.split(';').map((s) => s.trim()).find((s) => s.startsWith(SESSION_COOKIE + '='));
  if (!match) return null;
  const token = match.slice(SESSION_COOKIE.length + 1);
  const session = sessions.get(token);
  if (!session) return null;
  if (Date.now() > session.expires) {
    sessions.delete(token);
    return null;
  }
  return token;
}

function purgeExpired() {
  const now = Date.now();
  for (const [k, v] of sessions) {
    if (now > v.expires) sessions.delete(k);
  }
}

setInterval(purgeExpired, 15 * 60 * 1000).unref();

const proxy = httpProxy.createProxyServer({
  target: UPSTREAM,
  changeOrigin: true,
  ws: true,
});

proxy.on('error', (err, req, res) => {
  if (res && !res.headersSent) {
    res.writeHead(502, { 'Content-Type': 'text/plain' });
    res.end('Bad Gateway');
  }
});

const LOGOUT_SNIPPET =
  '<script>' +
  '(function(){' +
    'function injectCustomNav(){' +
      'if(document.getElementById("orthanc-worklist-link-li")) return;' +
      'var allElements = Array.from(document.querySelectorAll("a, button, li, div, span"));' +
      'var legacyEl = allElements.find(function(el){ return /legacy/i.test(el.textContent || ""); });' +
      'var targetLi = legacyEl ? (legacyEl.tagName === "LI" ? legacyEl : legacyEl.closest("li")) : null;' +
      'var targetUl = targetLi ? targetLi.parentElement : (document.querySelector("ul.nav") || document.querySelector("aside ul") || document.querySelector(".sidebar ul") || document.querySelector("nav ul") || document.querySelector("ul"));' +
      'if(!targetUl) return;' +
      
      'var wlLi = document.createElement("li");' +
      'wlLi.id = "orthanc-worklist-link-li";' +
      'wlLi.className = targetLi ? targetLi.className : "nav-item";' +
      'wlLi.style.listStyle = "none";' +
      'wlLi.style.marginTop = "4px";' +
      'wlLi.style.marginBottom = "4px";' +
      'wlLi.innerHTML = \'<a href="/worklist" style="display:flex;align-items:center;padding:10px 16px;color:#38bdf8;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;transition:background 0.2s;" onmouseover="this.style.background=\\\'rgba(56,189,248,0.15)\\\'" onmouseout="this.style.background=\\\'transparent\\\'"><i class="fa fa-solid fa-list-check fa-lg menu-icon" style="margin-right:12px;width:20px;text-align:center;color:#38bdf8;"></i><span>Worklist Dashboard</span></a>\';' +
      
      'if(targetLi){' +
        'targetUl.insertBefore(wlLi, targetLi);' +
      '} else {' +
        'targetUl.appendChild(wlLi);' +
      '}' +

      'if(!document.getElementById("orthanc-custom-settings-menu-item")){' +
        'var infoEl = allElements.find(function(el){ return /system info/i.test(el.textContent || ""); });' +
        'if(infoEl){' +
          'var infoLi = infoEl.tagName === "LI" ? infoEl : infoEl.closest("li");' +
          'if(infoLi && infoLi.parentElement){' +
            'var cfgLi = document.createElement("li");' +
            'cfgLi.id = "orthanc-custom-settings-menu-item";' +
            'cfgLi.style.listStyle = "none";' +
            'cfgLi.style.marginTop = "2px";' +
            'cfgLi.style.marginBottom = "2px";' +
            'cfgLi.innerHTML = \'<a href="/worklist?settings=1" style="display:flex;align-items:center;padding:8px 12px;color:#38bdf8;text-decoration:none;border-radius:6px;font-size:13.5px;font-weight:600;transition:background 0.2s;" onmouseover="this.style.background=\\\\\\\'rgba(56,189,248,0.15)\\\\\\\'" onmouseout="this.style.background=\\\\\\\'transparent\\\\\\\'"><i class="fa fa-solid fa-gears fa-lg menu-icon" style="margin-right:10px;width:18px;text-align:center;color:#38bdf8;"></i><span>Pengaturan HMS & Faskes</span></a>\';' +
            'infoLi.parentElement.insertBefore(cfgLi, infoLi);' +
          '}' +
        '}' +
      '}' +

      'if(!document.getElementById("orthanc-user-dropdown-li")){' +
        'var userLi = document.createElement("li");' +
        'userLi.id = "orthanc-user-dropdown-li";' +
        'userLi.className = targetLi ? targetLi.className : "nav-item";' +
        'userLi.style.listStyle = "none";' +
        'userLi.style.marginTop = "4px";' +
        'userLi.innerHTML = ' +
          '\'<div id="orthanc-user-menu-btn" role="button" tabindex="0" style="display:flex;align-items:center;padding:10px 16px;color:#e5e7eb;cursor:pointer;user-select:none;border-radius:6px;font-size:14px;font-weight:500;transition:background 0.2s;" onmouseover="this.style.background=\\\'rgba(255,255,255,0.08)\\\'" onmouseout="this.style.background=\\\'transparent\\\'">\' +' +
            '\'<i class="fa fa-solid fa-user fa-lg menu-icon" style="margin-right:12px;width:20px;text-align:center;color:#9ca3af;"></i>\' +' +
            '\'<span style="flex-grow:1;">orthanc</span>\' +' +
            '\'<i id="orthanc-user-caret" class="fa fa-chevron-down" style="font-size:12px;color:#9ca3af;transition:transform 0.2s;"></i>\' +' +
          '\'</div>\' +' +
          '\'<ul id="orthanc-user-submenu" style="display:none;list-style:none;padding-left:20px;margin:4px 0 8px 0;">\' +' +
            '\'<li style="margin-top:2px;">\' +' +
              '\'<a href="/logout" style="display:flex;align-items:center;padding:8px 12px;color:#f87171;text-decoration:none;border-radius:6px;font-size:13.5px;font-weight:500;transition:background 0.2s;" onmouseover="this.style.background=\\\'rgba(248,113,113,0.15)\\\'" onmouseout="this.style.background=\\\'transparent\\\'">\' +' +
                '\'<i class="fa fa-solid fa-right-from-bracket fa-lg menu-icon" style="margin-right:10px;width:18px;text-align:center;color:#f87171;"></i>\' +' +
                '\'<span>Keluar</span>\' +' +
              '\'</a>\' +' +
            '\'</li>\' +' +
          '\'</ul>\';' +
        'targetUl.appendChild(userLi);' +
        'var btn = userLi.querySelector("#orthanc-user-menu-btn");' +
        'var submenu = userLi.querySelector("#orthanc-user-submenu");' +
        'var caret = userLi.querySelector("#orthanc-user-caret");' +
        'if(btn && submenu && caret){' +
          'btn.addEventListener("click", function(e){' +
            'e.preventDefault(); e.stopPropagation();' +
            'var isOpen = submenu.style.display === "block";' +
            'submenu.style.display = isOpen ? "none" : "block";' +
            'caret.style.transform = isOpen ? "rotate(0deg)" : "rotate(180deg)";' +
          '});' +
        '}' +
      '}' +
    '}' +
    'setInterval(injectCustomNav, 400);' +
    'window.addEventListener("DOMContentLoaded", injectCustomNav);' +
  '})();' +
  '</script>';

function redirect(res, location, extraHeaders) {
  res.writeHead(302, Object.assign({ Location: location }, extraHeaders || {}));
  res.end();
}

proxy.on('proxyRes', (proxyRes, req, res) => {
  const injectHtml = String(proxyRes.headers['content-type'] || '').toLowerCase().includes('text/html');

  Object.keys(proxyRes.headers).forEach((key) => res.setHeader(key, proxyRes.headers[key]));
  res.statusCode = proxyRes.statusCode;
  res.statusMessage = proxyRes.statusMessage;

  if (injectHtml) {
    const chunks = [];
    proxyRes.on('data', (c) => chunks.push(Buffer.from(c)));
    proxyRes.on('end', () => {
      let body = Buffer.concat(chunks).toString('utf8');
      if (/<\/body\s*>/i.test(body)) {
        body = body.replace(/<\/body\s*>/i, (m) => LOGOUT_SNIPPET + m);
      }
      const buf = Buffer.from(body, 'utf8');
      res.removeHeader('Content-Length');
      res.setHeader('Content-Length', buf.length);
      res.end(buf);
    });
  } else {
    proxyRes.pipe(res);
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function serveLogin(res, errorMsg) {
  const html = fs.readFileSync(path.join(__dirname, 'views', 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, 'views', 'style.css'), 'utf8');
  const errorJson = errorMsg ? `<script>window.ORTHANC_ERROR=${JSON.stringify(escapeHtml(errorMsg))};<\/script>` : '';
  const rendered = html.replace('/*__CSS__*/', () => css).replace('/*__SERVER_ERROR__*/', () => errorJson);
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(rendered);
}

function serveWorklistUI(res) {
  const htmlPath = path.join(__dirname, 'views', 'worklist.html');
  if (fs.existsSync(htmlPath)) {
    const content = fs.readFileSync(htmlPath, 'utf8');
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(content);
  } else {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Worklist UI view not found');
  }
}

// System Settings Helpers
function loadSystemSettings() {
  let fileSettings = {};
  if (fs.existsSync(SETTINGS_FILE)) {
    try {
      fileSettings = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8'));
    } catch (e) {}
  }
  return {
    institutionName: fileSettings.institutionName || process.env.INSTITUTION_NAME || 'RSUD SIMRS',
    hmsHost: fileSettings.hmsHost || process.env.HMS_SQLSERVER_HOST || '103.167.236.130',
    hmsPort: fileSettings.hmsPort || process.env.HMS_SQLSERVER_PORT || '1433',
    hmsDb: fileSettings.hmsDb || process.env.HMS_SQLSERVER_DB || 'artha_medika',
    hmsUser: fileSettings.hmsUser || process.env.HMS_SQLSERVER_USER || 'hmsdb',
    hmsPassword: fileSettings.hmsPassword || process.env.HMS_SQLSERVER_PASSWORD || 'kisahkita',
    storagePath: fileSettings.storagePath || process.env.ORTHANC_STORAGE_PATH || '/var/lib/orthanc/db',
    webhookUrl: fileSettings.webhookUrl || process.env.SIMRS_WEBHOOK_URL || 'http://192.168.188.207:8090/api/radiology/notify-stored',
    pollInterval: fileSettings.pollInterval || process.env.POLL_INTERVAL_SECONDS || '5',
    pollDaysBack: fileSettings.pollDaysBack || process.env.POLL_DAYS_BACK || '1'
  };
}

function maskPassword(p) {
  if (!p) return '';
  if (p.length <= 4) return '****';
  return p.slice(0, 2) + '****' + p.slice(-2);
}

function saveSystemSettings(newSettings) {
  const current = loadSystemSettings();
  const updated = {
    institutionName: (newSettings.institutionName || current.institutionName).trim(),
    hmsHost: (newSettings.hmsHost || current.hmsHost).trim(),
    hmsPort: String(newSettings.hmsPort || current.hmsPort).trim(),
    hmsDb: (newSettings.hmsDb || current.hmsDb).trim(),
    hmsUser: (newSettings.hmsUser || current.hmsUser).trim(),
    hmsPassword: newSettings.hmsPassword && newSettings.hmsPassword.trim() ? newSettings.hmsPassword.trim() : current.hmsPassword,
    storagePath: (newSettings.storagePath || current.storagePath).trim(),
    webhookUrl: (newSettings.webhookUrl || current.webhookUrl).trim(),
    pollInterval: String(newSettings.pollInterval || current.pollInterval).trim(),
    pollDaysBack: String(newSettings.pollDaysBack || current.pollDaysBack).trim()
  };

  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(updated, null, 2), 'utf8');

  // Also update .env file if available
  const envPath = '/home/youy/Orthanc/.env';
  if (fs.existsSync(envPath)) {
    try {
      let envContent = fs.readFileSync(envPath, 'utf8');
      const updateEnvKey = (key, val) => {
        const regex = new RegExp(`^${key}=.*$`, 'm');
        if (regex.test(envContent)) {
          envContent = envContent.replace(regex, `${key}=${val}`);
        } else {
          envContent += `\n${key}=${val}`;
        }
      };
      updateEnvKey('INSTITUTION_NAME', updated.institutionName);
      updateEnvKey('HMS_SQLSERVER_HOST', updated.hmsHost);
      updateEnvKey('HMS_SQLSERVER_PORT', updated.hmsPort);
      updateEnvKey('HMS_SQLSERVER_DB', updated.hmsDb);
      updateEnvKey('HMS_SQLSERVER_USER', updated.hmsUser);
      updateEnvKey('HMS_SQLSERVER_PASSWORD', updated.hmsPassword);
      updateEnvKey('SIMRS_WEBHOOK_URL', updated.webhookUrl);
      updateEnvKey('POLL_INTERVAL_SECONDS', updated.pollInterval);
      updateEnvKey('POLL_DAYS_BACK', updated.pollDaysBack);
      fs.writeFileSync(envPath, envContent, 'utf8');
    } catch (e) {}
  }

  return updated;
}

function testHmsConnection(host, port, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(timeoutMs);
    socket.on('connect', () => {
      socket.destroy();
      resolve({ success: true, message: `Berhasil terhubung ke SQL Server ${host}:${port}!` });
    });
    socket.on('error', (err) => {
      socket.destroy();
      resolve({ success: false, message: `Gagal terhubung ke ${host}:${port} (${err.message})` });
    });
    socket.on('timeout', () => {
      socket.destroy();
      resolve({ success: false, message: `Koneksi ke ${host}:${port} RTO / Timeout (${timeoutMs}ms)` });
    });
    socket.connect(parseInt(port, 10), host);
  });
}

// Worklist API Helpers
function getWorklistItems() {
  const items = [];
  try {
    const files = fs.readdirSync(WORKLISTS_DIR);
    files.forEach((file) => {
      if (file.endsWith('.json') && file.startsWith('order_')) {
        try {
          const content = fs.readFileSync(path.join(WORKLISTS_DIR, file), 'utf8');
          const data = JSON.parse(content);
          data.filename = file;
          items.push(data);
        } catch (e) {}
      }
    });
  } catch (e) {}
  return items.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
}

function saveWorklistItem(data) {
  const settings = loadSystemSettings();
  const timestamp = Date.now();
  const dateStr = new Date().toISOString().replace('T', ' ').slice(0, 16);
  const acc = (data.accessionNumber || 'ACC-' + timestamp).replace(/[^a-zA-Z0-9_-]/g, '');
  const filename = `order_${acc}.json`;

  const item = {
    filename,
    timestamp,
    scheduledDate: dateStr,
    institutionName: data.institutionName || settings.institutionName || 'RSUD SIMRS',
    patientId: data.patientId || '',
    patientName: data.patientName || '',
    patientSex: data.patientSex || 'M',
    patientBirthDate: data.patientBirthDate || '',
    modality: data.modality || 'CR',
    scheduledAet: data.scheduledAet || 'MOD_XRAY',
    accessionNumber: acc,
    procedureDescription: data.procedureDescription || 'General Examination',
    doctorName: data.doctorName || 'DR. SIMRS PHYSICIAN',
  };

  fs.writeFileSync(path.join(WORKLISTS_DIR, filename), JSON.stringify(item, null, 2), 'utf8');

  return item;
}

// Blacklist helpers
function loadBlacklist() {
  try {
    if (fs.existsSync(BLACKLIST_FILE)) {
      return JSON.parse(fs.readFileSync(BLACKLIST_FILE, 'utf8'));
    }
  } catch (e) {}
  return { dismissed: [] };
}

function addToBlacklist(accessionNumber) {
  const bl = loadBlacklist();
  if (!bl.dismissed) bl.dismissed = [];
  if (!bl.dismissed.includes(accessionNumber)) {
    bl.dismissed.push(accessionNumber);
    fs.writeFileSync(BLACKLIST_FILE, JSON.stringify(bl, null, 2), 'utf8');
  }
}

function removeFromBlacklist(accessionNumber) {
  const bl = loadBlacklist();
  if (!bl.dismissed) bl.dismissed = [];
  bl.dismissed = bl.dismissed.filter(a => a !== accessionNumber);
  fs.writeFileSync(BLACKLIST_FILE, JSON.stringify(bl, null, 2), 'utf8');
}

function deleteWorklistItem(filename) {
  const safeName = path.basename(filename);
  const jsonPath = path.join(WORKLISTS_DIR, safeName);
  const wlPath = jsonPath.replace(/\.json$/, '.wl');

  // Read accession before deleting
  let accessionNumber = null;
  try {
    if (fs.existsSync(jsonPath)) {
      const data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
      accessionNumber = data.accessionNumber || null;
    }
  } catch (e) {}

  if (fs.existsSync(jsonPath)) fs.unlinkSync(jsonPath);
  if (fs.existsSync(wlPath)) fs.unlinkSync(wlPath);

  // Blacklist so polling engine won't recreate it
  if (accessionNumber) addToBlacklist(accessionNumber);
}

const server = http.createServer(async (req, res) => {
  const urlPath = new URL(req.url, 'http://x').pathname;

  if (req.method === 'POST' && urlPath === '/login') {
    let body;
    try {
      body = await parseBody(req);
    } catch (e) {
      res.writeHead(400, { 'Content-Type': 'text/plain' });
      res.end('Bad request');
      return;
    }
    const params = new URLSearchParams(body);
    const user = (params.get('username') || '').trim();
    const pass = params.get('password') || '';

    if (!user || !pass) {
      serveLogin(res, 'Username dan password wajib diisi.');
      return;
    }
    if (await validateCredentials(user, pass)) {
      const token = createSession(makeBasicAuth(user, pass));
      redirect(res, '/', {
        'Set-Cookie': `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax`,
      });
    } else {
      serveLogin(res, 'Kredensial tidak valid. Silakan coba lagi.');
    }
    return;
  }

  if (req.method === 'GET' && urlPath === '/logout') {
    const token = getSession(req);
    if (token) sessions.delete(token);
    redirect(res, '/', {
      'Set-Cookie': `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`,
    });
    return;
  }

  const token = getSession(req);
  if (!token) {
    serveLogin(res);
    return;
  }

  // Worklist Dashboard UI Route
  if (req.method === 'GET' && urlPath === '/worklist') {
    serveWorklistUI(res);
    return;
  }

  // System Settings REST API Routes
  if (req.method === 'GET' && urlPath === '/api/settings') {
    const settings = loadSystemSettings();
    const responseData = Object.assign({}, settings, {
      hmsPasswordMasked: maskPassword(settings.hmsPassword),
      hmsPassword: '' // keep clear in GET
    });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(responseData));
    return;
  }

  if (req.method === 'POST' && urlPath === '/api/settings') {
    let body;
    try {
      body = await parseBody(req);
      const data = JSON.parse(body);
      const saved = saveSystemSettings(data);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        success: true,
        message: 'Pengaturan sistem berhasil disimpan dan diperbarui!',
        settings: Object.assign({}, saved, { hmsPasswordMasked: maskPassword(saved.hmsPassword), hmsPassword: '' })
      }));
    } catch (e) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ success: false, error: e.message }));
    }
    return;
  }

  if (req.method === 'POST' && urlPath === '/api/settings/test-hms') {
    let body;
    try {
      body = await parseBody(req);
      const data = JSON.parse(body);
      const host = (data.hmsHost || '103.167.236.130').trim();
      const port = (data.hmsPort || '1433').trim();
      const testResult = await testHmsConnection(host, port);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(testResult));
    } catch (e) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ success: false, message: e.message }));
    }
    return;
  }

  // Worklist REST API Routes
  if (req.method === 'GET' && urlPath === '/api/worklists') {
    const items = getWorklistItems();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(items));
    return;
  }

  if (req.method === 'POST' && urlPath === '/api/worklists') {
    let body;
    try {
      body = await parseBody(req);
      const data = JSON.parse(body);
      const saved = saveWorklistItem(data);
      res.writeHead(201, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(saved));
    } catch (e) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  // GET blacklist
  if (req.method === 'GET' && urlPath === '/api/worklists/blacklist') {
    const bl = loadBlacklist();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(bl));
    return;
  }

  // DELETE from blacklist (restore order)
  if (req.method === 'DELETE' && urlPath.startsWith('/api/worklists/blacklist/')) {
    const acc = decodeURIComponent(urlPath.replace('/api/worklists/blacklist/', ''));
    try {
      removeFromBlacklist(acc);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ success: true, message: `Accession ${acc} dihapus dari blacklist` }));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  if (req.method === 'DELETE' && urlPath.startsWith('/api/worklists/')) {
    const filename = decodeURIComponent(urlPath.replace('/api/worklists/', ''));
    try {
      deleteWorklistItem(filename);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ success: true, message: 'Order dihapus dan di-blacklist dari polling HMS.' }));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  // Proxy all other requests to Orthanc core
  const session = sessions.get(token);
  req.headers['Authorization'] = session.creds;
  req.headers['connection'] = 'keep-alive';

  proxy.web(req, res, { selfHandleResponse: true });
});

server.on('upgrade', (req, socket, head) => {
  const token = getSession(req);
  const session = token ? sessions.get(token) : null;
  if (!session) {
    socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
    socket.destroy();
    return;
  }
  req.headers['Authorization'] = session.creds;
  proxy.ws(req, socket, head);
});

server.listen(PROXY_PORT, () => {
  console.log(`Orthanc auth proxy listening on ${PROXY_PORT} -> ${UPSTREAM}`);
});