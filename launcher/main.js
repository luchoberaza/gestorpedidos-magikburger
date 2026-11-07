const { app, BrowserWindow, dialog } = require("electron");
const path = require("path");
const fs = require("fs");
const https = require("https");
const { spawn } = require("child_process");
const net = require("net");

// =======================
// Config actualizaciones (OTA)
// =======================
// Repo PÚBLICO que sirve de canal de actualización. La Action publica ahí una
// "release" con un asset 'backend.json' (los archivos de texto del backend) y un
// tag tipo 'b<numero>' (build monotónico). El launcher la baja al abrir.
const UPDATE_REPO = "luchoberaza/gestorpedidos-magikburger";
const OTA_TOTAL_BUDGET_MS = 20000; // tope total para no demorar el arranque

// =======================
// Logging (siempre)
// =======================
function getUserDataDir() {
  return app.getPath("userData");
}

function getLogPath() {
  return path.join(getUserDataDir(), "launcher.log");
}

function log(...args) {
  try {
    const dir = getUserDataDir();
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(
      getLogPath(),
      `[${new Date().toISOString()}] ${args.map(String).join(" ")}\n`,
      "utf-8"
    );
  } catch {}
}

process.on("uncaughtException", (err) => {
  log("uncaughtException:", err?.stack || err?.message || String(err));
});

process.on("unhandledRejection", (reason) => {
  log("unhandledRejection:", reason?.stack || reason?.message || String(reason));
});

// =======================
// Utils puerto
// =======================
function isPortOpen(host, port, timeoutMs = 500) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let done = false;

    const finish = (ok) => {
      if (done) return;
      done = true;
      try { socket.destroy(); } catch {}
      resolve(ok);
    };

    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
    socket.connect(port, host);
  });
}

async function waitForPort(host, port, totalMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < totalMs) {
    const ok = await isPortOpen(host, port, 500);
    if (ok) return true;
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

function parseUrl(baseUrl) {
  const u = new URL(baseUrl);
  const host = u.hostname || "127.0.0.1";
  const port = u.port ? parseInt(u.port, 10) : 80;
  return { host, port };
}

// =======================
// Rutas persistentes (datos + backends OTA)
// =======================
function getDataDir() {
  const d = path.join(getUserDataDir(), "data");
  fs.mkdirSync(d, { recursive: true });
  return d;
}

function getDbPath() {
  return path.join(getDataDir(), "magikburger.db");
}

function getBackendsDir() {
  const d = path.join(getUserDataDir(), "backends");
  fs.mkdirSync(d, { recursive: true });
  return d;
}

function activePointerPath() {
  return path.join(getBackendsDir(), "active.txt");
}

function activeBuildPath() {
  return path.join(getBackendsDir(), "active_build.txt");
}

function getLocalBuild() {
  try {
    const v = parseInt(fs.readFileSync(activeBuildPath(), "utf-8").trim(), 10);
    return Number.isFinite(v) ? v : 0;
  } catch {
    return 0;
  }
}

function setActiveBackend(dir, build) {
  fs.writeFileSync(activePointerPath(), dir, "utf-8");
  fs.writeFileSync(activeBuildPath(), String(build), "utf-8");
}

// Carpeta del backend EMPAQUETADO (lectura): siempre completa y funcional.
function bundledBackendDir() {
  if (app.isPackaged) return path.join(process.resourcesPath, "backend_app");
  return path.join(__dirname, ".."); // dev: el proyecto está un nivel arriba
}

// Carpeta del backend ACTIVO: la actualizada si existe y es válida; si no, el bundle.
function getActiveBackendDir() {
  try {
    const p = fs.readFileSync(activePointerPath(), "utf-8").trim();
    if (p && fs.existsSync(path.join(p, "app.py"))) return p;
  } catch {}
  return bundledBackendDir();
}

// Asegura la DB en la carpeta persistente. La primera vez la siembra desde el bundle
// SOLO si todavía no existe (así nunca pisa la base real del cliente).
function ensureDbInUserData() {
  const dbPath = getDbPath();
  if (!fs.existsSync(dbPath)) {
    const seed = path.join(bundledBackendDir(), "magikburger.db");
    try {
      if (fs.existsSync(seed)) {
        fs.copyFileSync(seed, dbPath);
        log("DB sembrada en", dbPath);
      } else {
        log("No había DB semilla en el bundle; se creará vacía al migrar el esquema.");
      }
    } catch (e) {
      log("Error sembrando DB:", e?.message || String(e));
    }
  }
  return dbPath;
}

// =======================
// HTTPS helpers (solo módulos nativos)
// =======================
function httpsGetBuffer(url, { headers = {}, timeoutMs = 8000, maxRedirects = 5 } = {}) {
  return new Promise((resolve, reject) => {
    const doReq = (u, redirectsLeft) => {
      let req;
      try {
        req = https.get(u, { headers: { "User-Agent": "MagikBurgerLauncher", ...headers } }, (res) => {
          const sc = res.statusCode || 0;
          if ([301, 302, 303, 307, 308].includes(sc) && res.headers.location && redirectsLeft > 0) {
            res.resume();
            return doReq(res.headers.location, redirectsLeft - 1);
          }
          if (sc < 200 || sc >= 300) {
            res.resume();
            return reject(new Error("HTTP " + sc));
          }
          const chunks = [];
          res.on("data", (c) => chunks.push(c));
          res.on("end", () => resolve(Buffer.concat(chunks)));
        });
      } catch (e) {
        return reject(e);
      }
      req.setTimeout(timeoutMs, () => { try { req.destroy(new Error("timeout")); } catch {} });
      req.on("error", reject);
    };
    doReq(url, maxRedirects);
  });
}

async function httpsGetJson(url, opts) {
  const buf = await httpsGetBuffer(url, { headers: { Accept: "application/vnd.github+json" }, ...(opts || {}) });
  return JSON.parse(buf.toString("utf-8"));
}

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error("OTA budget agotado")), ms)),
  ]);
}

// Copia recursiva del bundle a un dir nuevo, salteando la DB.
function copyBundleExceptDb(src, dst) {
  fs.rmSync(dst, { recursive: true, force: true });
  fs.cpSync(src, dst, {
    recursive: true,
    filter: (s) => path.basename(s) !== "magikburger.db",
  });
}

// =======================
// OTA: baja la última versión del backend y la deja lista para usar.
// Cualquier error -> no toca nada (se sigue con el backend actual/bundle).
// =======================
async function checkAndApplyUpdate() {
  const localBuild = getLocalBuild();

  const rel = await httpsGetJson(
    `https://api.github.com/repos/${UPDATE_REPO}/releases/latest`,
    { timeoutMs: 7000 }
  );

  const tag = String(rel.tag_name || "");
  const remoteBuild = parseInt(tag.replace(/[^\d]/g, ""), 10);
  if (!Number.isFinite(remoteBuild) || remoteBuild <= 0) {
    return { applied: false, reason: "release sin build numérico" };
  }
  if (remoteBuild <= localBuild) {
    return { applied: false, reason: `al día (local ${localBuild} >= remoto ${remoteBuild})` };
  }

  const asset = (rel.assets || []).find((a) => a.name === "backend.json");
  if (!asset || !asset.browser_download_url) {
    return { applied: false, reason: "release sin asset backend.json" };
  }

  const buf = await httpsGetBuffer(asset.browser_download_url, { timeoutMs: 15000 });
  const data = JSON.parse(buf.toString("utf-8"));
  if (!data || typeof data.files !== "object" || !data.files["app.py"]) {
    throw new Error("backend.json inválido o sin app.py");
  }

  // Construimos el nuevo backend: bundle completo + overlay de archivos actualizados.
  const newDir = path.join(getBackendsDir(), String(remoteBuild));
  copyBundleExceptDb(bundledBackendDir(), newDir);

  for (const [relPath, content] of Object.entries(data.files)) {
    // Seguridad: no permitir rutas que escapen del dir.
    const safe = path.normalize(relPath).replace(/^(\.\.[/\\])+/, "");
    if (safe.includes("..")) continue;
    const dest = path.join(newDir, safe);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, String(content), "utf-8");
  }

  if (!fs.existsSync(path.join(newDir, "app.py"))) {
    throw new Error("la actualización no dejó app.py");
  }

  setActiveBackend(newDir, remoteBuild);

  // Limpieza: dejamos a lo sumo las 2 builds más nuevas.
  try {
    const dirs = fs.readdirSync(getBackendsDir())
      .map((n) => ({ n, v: parseInt(n, 10) }))
      .filter((x) => Number.isFinite(x.v))
      .sort((a, b) => b.v - a.v);
    dirs.slice(2).forEach((x) => {
      try { fs.rmSync(path.join(getBackendsDir(), x.n), { recursive: true, force: true }); } catch {}
    });
  } catch {}

  return { applied: true, build: remoteBuild };
}

// =======================
// Backend autostart
// =======================
let backendProc = null;

// Python embebido que viaja con el instalador (resources/python).
function bundledPython() {
  if (!app.isPackaged || process.platform !== "win32") return null;
  const p = path.join(process.resourcesPath, "python", "pythonw.exe");
  return fs.existsSync(p) ? p : null;
}

function stopBackend() {
  if (!backendProc) return;
  try {
    log("Stopping backend...");
    backendProc.kill();
  } catch (e) {
    log("stopBackend error:", e?.message || String(e));
  } finally {
    backendProc = null;
  }
}

async function spawnBackend(baseUrl, scriptDir, dbPath) {
  const { host, port } = parseUrl(baseUrl);

  // Si ya hay algo escuchando, no levantamos otro
  const already = await isPortOpen(host, port, 400);
  if (already) {
    log("Backend ya estaba levantado en", host, port);
    return { ok: true, reused: true };
  }

  const scriptPath = path.join(scriptDir, "app.py");
  const cwd = scriptDir;

  log("backendDir:", cwd);
  log("backend app.py:", scriptPath);
  log("db:", dbPath);
  log("target:", baseUrl);

  if (!fs.existsSync(scriptPath)) {
    return { ok: false, error: `No existe app.py en: ${scriptPath}` };
  }

  // Cargar app.py desde archivo y ejecutar app.run sin reloader
  const pyCode = [
    "import importlib.util",
    `p=r'''${scriptPath.replace(/\\/g, "\\\\")}'''`,
    "spec=importlib.util.spec_from_file_location('mb_app', p)",
    "m=importlib.util.module_from_spec(spec)",
    "spec.loader.exec_module(m)",
    `m.app.run(host=r'''${host}''', port=int(${port}), debug=False, use_reloader=False)`
  ].join("; ");

  // Primero el Python que trae el instalador (embebido, con todas las dependencias):
  // la PC no necesita tener Python. El del sistema queda solo como respaldo.
  const sistema = process.platform === "win32"
    ? ["pythonw", "python", "py"]
    : ["python3", "python"];
  const embebido = bundledPython();
  const candidates = embebido ? [embebido, ...sistema] : sistema;

  // Pasamos la ruta de la DB por entorno (carpeta persistente del usuario).
  const childEnv = { ...process.env };
  if (dbPath) childEnv.MAGIK_DB_PATH = dbPath;

  let lastErr = null;

  for (const cmd of candidates) {
    try {
      const args = (cmd === "py") ? ["-3", "-c", pyCode] : ["-c", pyCode];

      log("Spawning backend:", cmd, args.join(" "));
      backendProc = spawn(cmd, args, {
        cwd,
        env: childEnv,
        windowsHide: true,
        stdio: "ignore",
        detached: false,
      });

      backendProc.on("error", (e) => {
        lastErr = e;
        log("backendProc error:", e?.message || String(e));
      });

      // El primer arranque en una PC nueva puede tardar más (antivirus revisando archivos).
      const up = await waitForPort(host, port, cmd === embebido ? 45000 : 15000);
      if (up) {
        log("Backend UP:", host, port);
        return { ok: true, reused: false };
      }

      try { backendProc.kill(); } catch {}
      backendProc = null;
      log("Backend no levantó con", cmd);
    } catch (e) {
      lastErr = e;
      log("spawnBackend exception:", e?.message || String(e));
    }
  }

  return { ok: false, error: lastErr ? (lastErr.message || String(lastErr)) : "No se pudo iniciar el backend." };
}

// =======================
// Ventana (IMPORTANTE: guardar referencia global)
// =======================
let mainWindow = null;

const BASE_URL = "http://127.0.0.1:5000";

// Pantalla que se ve mientras se busca la actualización y arranca el servidor.
// La app se carga recién cuando el servidor responde (antes se cargaba enseguida y
// salía "No pude cargar la interfaz" hasta volver a abrirla).
const PANTALLA_ARRANQUE = "data:text/html;charset=utf-8," + encodeURIComponent(`<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Magik</title><style>
html,body{height:100%;margin:0;background:#05060d;color:#e7e9ee;font-family:"Segoe UI",system-ui,sans-serif}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px}
.anillo{width:40px;height:40px;border-radius:50%;border:4px solid rgba(255,255,255,.1);border-top-color:#d946ef;animation:g .9s linear infinite}
@keyframes g{to{transform:rotate(360deg)}}
h1{margin:0;font-size:26px;font-weight:700;background:linear-gradient(135deg,#8b5cf6 0%,#d946ef 55%,#ec4899 100%);-webkit-background-clip:text;background-clip:text;color:transparent}
p{margin:0;font-size:14px;color:#9aa3b2}
</style></head><body><div class="anillo"></div><h1>Magik</h1><p>Abriendo…</p></body></html>`);

function createWindow() {
  const baseUrl = BASE_URL;

  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    backgroundColor: "#05060d",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.once("ready-to-show", () => win.show());

  // Evitar que se abra una ventana/app nueva (ej: al imprimir tickets).
  // La impresión se hace dentro de la misma ventana (iframe oculto).
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));

  // Primero la pantalla de arranque; la UI real se carga cuando el backend está arriba.
  win.loadURL(PANTALLA_ARRANQUE);

  win.webContents.on("did-fail-load", async (_e, code, desc, url, isMainFrame) => {
    // -3 = navegación interrumpida por otra (no es un error).
    if (!isMainFrame || code === -3) return;
    log("did-fail-load:", code, desc, url);
    await dialog.showMessageBox(win, {
      type: "error",
      title: "MagikBurger Launcher",
      message:
        "No pude cargar la interfaz.\n\n" +
        `URL: ${baseUrl}\n\n` +
        `Error: (${code}) ${desc}\n\n` +
        "Log:\n" + getLogPath(),
      buttons: ["OK"],
    });
  });

  return win;
}

// =======================
// Main
// =======================
// Una sola instancia: si la abren de nuevo (doble clic dos veces), se trae al frente
// la ventana que ya está abierta en vez de arrancar otro servidor.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
  app.whenReady().then(iniciar);
}

async function iniciar() {
  log("=== Launcher start ===");
  log("version:", app.getVersion());
  log("userData:", getUserDataDir());
  log("log:", getLogPath());
  log("isPackaged:", app.isPackaged);

  // Creamos ventana PRIMERO y la guardamos (para que no muera la app)
  mainWindow = createWindow();

  const baseUrl = BASE_URL;

  // Resolución de DB y backend según modo
  let dbPath = null;
  let activeDir = bundledBackendDir();

  if (app.isPackaged) {
    // DB persistente (nunca la pisa una actualización)
    dbPath = ensureDbInUserData();

    // Buscar/aplicar actualización del backend (con tope de tiempo y fallback total)
    try {
      const r = await withTimeout(checkAndApplyUpdate(), OTA_TOTAL_BUDGET_MS);
      log("OTA:", JSON.stringify(r));
    } catch (e) {
      log("OTA error (se sigue con la versión actual):", e?.message || String(e));
    }

    activeDir = getActiveBackendDir();
  } else {
    // Dev: como siempre (DB y backend al lado del proyecto)
    dbPath = path.join(bundledBackendDir(), "magikburger.db");
    activeDir = bundledBackendDir();
  }

  // Intentamos levantar backend
  const started = await spawnBackend(baseUrl, activeDir, dbPath);

  if (started.ok) {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.loadURL(baseUrl);
    return;
  }

  log("Backend start failed:", started.error || "unknown");
  await dialog.showMessageBox(mainWindow, {
    type: "error",
    title: "MagikBurger Launcher",
    message:
      "No se pudo iniciar Magik.\n\n" +
      "Probá cerrar y volver a abrir. Si sigue igual, volvé a ejecutar el instalador " +
      "(Instalar-Magik.exe): los datos no se pierden.\n\n" +
      "Detalle:\n" + (started.error || "desconocido") + "\n\n" +
      "Log:\n" + getLogPath(),
    buttons: ["OK"],
  });
}

app.on("before-quit", () => {
  stopBackend();
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});
