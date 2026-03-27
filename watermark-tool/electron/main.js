/**
 * Electron main process for Watermark Tool.
 *
 * Responsibilities:
 *  - Spawn the Python FastAPI backend (python/server.py via venv) on startup
 *  - Create the BrowserWindow and load renderer/index.html
 *  - Kill the backend process on app quit
 *  - Expose a save-file dialog via IPC so the renderer can trigger a native
 *    "Save As" sheet and write the downloaded bytes to disk
 */

"use strict";

const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const path   = require("path");
const fs     = require("fs");
const { spawn } = require("child_process");

// ── constants ──────────────────────────────────────────────────────────────

const API_BASE   = "http://127.0.0.1:8765";
const PYTHON_DIR = path.join(__dirname, "..", "python");

// ── backend process ────────────────────────────────────────────────────────

let backendProc = null;

/**
 * Return [executable, args, cwd] for the Python backend.
 *
 * Packaged app  → single-file PyInstaller binary from process.resourcesPath
 * Dev           → venv Python running server.py from the source tree
 */
function resolveBackend() {
  if (app.isPackaged) {
    // electron-builder copies python/dist/server.exe into
    // <app>/resources/python/dist/server.exe
    const exe = path.join(
      process.resourcesPath,
      "python", "dist",
      process.platform === "win32" ? "server.exe" : "server"
    );
    return { exe, args: [], cwd: path.dirname(exe) };
  }

  // Dev: run via venv Python
  const winPy = path.join(PYTHON_DIR, "venv", "Scripts", "python.exe");
  const nixPy = path.join(PYTHON_DIR, "venv", "bin", "python");
  const pyExe = fs.existsSync(winPy) ? winPy
              : fs.existsSync(nixPy) ? nixPy
              : "python";
  return { exe: pyExe, args: [path.join(PYTHON_DIR, "server.py")], cwd: PYTHON_DIR };
}

function startBackend() {
  const { exe, args, cwd } = resolveBackend();

  console.log(`[main] Starting backend: ${exe} ${args.join(" ")}`);
  backendProc = spawn(exe, args, {
    cwd,
    stdio: ["ignore", "pipe", "pipe"],
  });

  backendProc.stdout.on("data", (d) => process.stdout.write(`[py] ${d}`));
  backendProc.stderr.on("data", (d) => process.stderr.write(`[py] ${d}`));

  backendProc.on("exit", (code, sig) => {
    console.log(`[main] Backend exited: code=${code} signal=${sig}`);
    backendProc = null;
  });
}

function stopBackend() {
  if (backendProc) {
    try { backendProc.kill(); } catch (_) {}
    backendProc = null;
  }
}

/**
 * Poll /health until the server is ready (max ~15 s).
 */
async function waitForBackend(maxMs = 15_000, intervalMs = 300) {
  const { net } = require("electron");
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    try {
      await new Promise((resolve, reject) => {
        const req = net.request(`${API_BASE}/health`);
        req.on("response", (res) => {
          if (res.statusCode === 200) resolve();
          else reject(new Error(`status ${res.statusCode}`));
        });
        req.on("error", reject);
        req.end();
      });
      return true; // server is up
    } catch (_) {
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  }
  return false;
}

// ── window ─────────────────────────────────────────────────────────────────

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width:  900,
    height: 640,
    minWidth:  760,
    minHeight: 520,
    title: "Watermark Tool",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));

  mainWindow.on("closed", () => { mainWindow = null; });
}

// ── app lifecycle ──────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  startBackend();

  const ready = await waitForBackend();
  if (!ready) {
    console.error("[main] Backend did not start in time — opening anyway");
  }

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => stopBackend());

// ── IPC handlers ───────────────────────────────────────────────────────────

/**
 * Show a native Save dialog and write the file.
 * Invoked by renderer via window.api.saveFile({ filename, buffer }).
 */
ipcMain.handle("save-file", async (_event, { filename, buffer }) => {
  const { canceled, filePath } = await dialog.showSaveDialog(mainWindow, {
    defaultPath: filename,
    filters: [
      { name: "MP4 Video", extensions: ["mp4"] },
      { name: "All Files",  extensions: ["*"] },
    ],
  });

  if (canceled || !filePath) return { success: false, canceled: true };

  try {
    fs.writeFileSync(filePath, Buffer.from(buffer));
    return { success: true, filePath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

/**
 * Expose the API base URL so the renderer doesn't have to hard-code it.
 */
ipcMain.handle("get-api-base", () => API_BASE);

/**
 * Decode a visible DITZY-ID hex string via the Python /decode-hex endpoint.
 * @param {Electron.IpcMainInvokeEvent} _event
 * @param {string} hexId  raw hex or "DITZY-ID:AABB..." string
 */
ipcMain.handle("decode-hex", async (_event, hexId) => {
  const http = require("http");
  const body = JSON.stringify({ hex_id: hexId });
  return new Promise((resolve) => {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port:     8765,
        path:     "/decode-hex",
        method:   "POST",
        headers:  {
          "Content-Type":   "application/json",
          "Content-Length": Buffer.byteLength(body),
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => { data += chunk; });
        res.on("end",  () => {
          try   { resolve(JSON.parse(data)); }
          catch { resolve({ success: false, username: null, error: "Invalid JSON from server" }); }
        });
      }
    );
    req.on("error", (err) => resolve({ success: false, username: null, error: err.message }));
    req.write(body);
    req.end();
  });
});
