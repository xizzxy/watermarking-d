/**
 * Renderer process logic for Watermark Tool.
 *
 * All communication with the Python backend goes through fetch() to
 * http://127.0.0.1:8765.  File saving is delegated back to the main
 * process via window.api.saveFile() (contextBridge / IPC).
 */

"use strict";

// ── API base ───────────────────────────────────────────────────────────────

let API_BASE = "http://127.0.0.1:8765";

(async () => {
  try {
    API_BASE = await window.api.getApiBase();
  } catch (_) { /* already set to default */ }
  pollHealth();
})();

// ── backend health poll ────────────────────────────────────────────────────

const backendDot   = document.getElementById("backendDot");
const backendLabel = document.getElementById("backendLabel");

async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) });
    if (res.ok) {
      const json = await res.json();
      backendDot.className     = "dot ok";
      backendLabel.textContent = `backend ready (${json.version || "v?"})`;
      return true;
    }
  } catch (_) {}
  backendDot.className     = "dot error";
  backendLabel.textContent = "backend offline";
  return false;
}

function pollHealth() {
  checkHealth();
  setInterval(checkHealth, 5000);
}

// ── tab switching ──────────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t)   => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
  });
});

// ── generic drop-zone helper ───────────────────────────────────────────────

/**
 * Wire up a drop zone + hidden file input.
 * @param {Object} opts
 *   dropZone   HTMLElement
 *   fileInput  HTMLInputElement
 *   labelEl    HTMLElement  (shows chosen filename)
 *   button     HTMLButtonElement
 *   onFile     (File) => void
 */
function setupDropZone({ dropZone, fileInput, labelEl, button, onFile }) {
  const ACCEPTED = new Set(["video/mp4", "video/quicktime"]);

  function handleFile(file) {
    if (!file) return;
    const ext = file.name.split(".").pop().toLowerCase();
    if (!["mp4", "mov"].includes(ext)) {
      labelEl.textContent = "⚠ Only .mp4 and .mov are accepted";
      button.disabled = true;
      return;
    }
    labelEl.textContent = file.name;
    button.disabled = false;
    onFile(file);
  }

  dropZone.addEventListener("click",  () => fileInput.click());
  dropZone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) handleFile(fileInput.files[0]);
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  });
}

// ── result-card helpers ────────────────────────────────────────────────────

function showSuccess(card, titleEl, bodyEl, title, body) {
  card.className    = "result-card success visible";
  titleEl.textContent = title;
  bodyEl.innerHTML    = body;
}

function showError(card, titleEl, bodyEl, title, body) {
  card.className    = "result-card error visible";
  titleEl.textContent = title;
  if (body !== "") bodyEl.innerHTML = body;
}

function hideResult(card) { card.className = "result-card"; }

function showProgress(wrap, label) {
  wrap.classList.add("visible");
  if (label) label.textContent = label.textContent; // preserve existing text
}

function hideProgress(wrap) { wrap.classList.remove("visible"); }

// ── ENCODE tab ─────────────────────────────────────────────────────────────

const encBtn           = document.getElementById("encBtn");
const encUsername      = document.getElementById("encUsername");
const encProgress      = document.getElementById("encProgress");
const encProgressLabel = document.getElementById("encProgressLabel");
const encResult        = document.getElementById("encResult");
const encResultTitle   = document.getElementById("encResultTitle");
const encResultBody    = document.getElementById("encResultBody");
const encResultActions = document.getElementById("encResultActions");

let encFile = null;

setupDropZone({
  dropZone:  document.getElementById("encDropZone"),
  fileInput: document.getElementById("encFileInput"),
  labelEl:   document.getElementById("encFilename"),
  button:    encBtn,
  onFile:    (f) => { encFile = f; updateEncBtn(); },
});

encUsername.addEventListener("input", updateEncBtn);

function updateEncBtn() {
  encBtn.disabled = !(encFile && encUsername.value.trim().length > 0);
}

encBtn.addEventListener("click", async () => {
  if (!encFile || !encUsername.value.trim()) return;

  hideResult(encResult);
  encResultActions.innerHTML = "";
  encProgressLabel.textContent = "Uploading and processing — this may take a minute…";
  showProgress(encProgress);
  encBtn.disabled = true;

  try {
    const formData = new FormData();
    formData.append("video",    encFile,                encFile.name);
    formData.append("username", encUsername.value.trim());

    const res = await fetch(`${API_BASE}/encode`, { method: "POST", body: formData });
    const json = await res.json();

    hideProgress(encProgress);

    if (!res.ok || !json.success) {
      const errText = json.detail || json.error || "Unknown error";
      encResultBody.textContent = errText;
      showError(encResult, encResultTitle, encResultBody, "Encoding failed", "");
      return;
    }

    // Download the encoded file immediately
    encProgressLabel.textContent = "Downloading encoded file…";
    showProgress(encProgress);

    const dlRes = await fetch(`${API_BASE}/download/${json.download_token}`);
    if (!dlRes.ok) {
      hideProgress(encProgress);
      showError(encResult, encResultTitle, encResultBody,
        "Download failed", `HTTP ${dlRes.status}`);
      return;
    }

    const blob       = await dlRes.blob();
    const arrayBuf   = await blob.arrayBuffer();
    const outName    = encFile.name.replace(/\.[^.]+$/, "") + "_watermarked.mp4";

    hideProgress(encProgress);

    // Ask main process to show Save dialog
    const saveRes = await window.api.saveFile({ filename: outName, buffer: arrayBuf });

    if (saveRes.canceled) {
      showSuccess(encResult, encResultTitle, encResultBody,
        "Watermark embedded",
        "File ready — save dialog was dismissed. The file was not written to disk.");
      return;
    }

    if (!saveRes.success) {
      showError(encResult, encResultTitle, encResultBody,
        "Save failed", escHtml(saveRes.error || "Unknown error"));
      return;
    }

    showSuccess(encResult, encResultTitle, encResultBody,
      "Watermark embedded",
      `Saved to: <code style="font-size:12px;word-break:break-all">${escHtml(saveRes.filePath)}</code>`);

    // Trigger cleanup in background
    fetch(`${API_BASE}/cleanup`, { method: "POST" }).catch(() => {});

  } catch (err) {
    hideProgress(encProgress);
    showError(encResult, encResultTitle, encResultBody,
      "Unexpected error", escHtml(err.message));
  } finally {
    updateEncBtn();
  }
});

// ── DECODE tab ─────────────────────────────────────────────────────────────

const decBtn         = document.getElementById("decBtn");
const decProgress    = document.getElementById("decProgress");
const decResult      = document.getElementById("decResult");
const decResultTitle = document.getElementById("decResultTitle");
const decResultBody  = document.getElementById("decResultBody");

let decFile = null;

setupDropZone({
  dropZone:  document.getElementById("decDropZone"),
  fileInput: document.getElementById("decFileInput"),
  labelEl:   document.getElementById("decFilename"),
  button:    decBtn,
  onFile:    (f) => { decFile = f; },
});

decBtn.addEventListener("click", async () => {
  if (!decFile) return;

  hideResult(decResult);
  showProgress(decProgress);
  decBtn.disabled = true;

  try {
    const formData = new FormData();
    formData.append("video", decFile, decFile.name);

    const res  = await fetch(`${API_BASE}/decode`, { method: "POST", body: formData });
    const json = await res.json();

    hideProgress(decProgress);

    if (!res.ok || !json.success) {
      showError(decResult, decResultTitle, decResultBody,
        "Decode failed",
        escHtml(json.detail || json.error || "Unknown error"));
      return;
    }

    if (json.username) {
      showSuccess(decResult, decResultTitle, decResultBody,
        "Fan identified",
        `Username: <span class="username-badge">${escHtml(json.username)}</span>`);
    } else {
      showSuccess(decResult, decResultTitle, decResultBody,
        "No watermark found",
        "This video does not contain a recognised steganographic watermark.");
    }

  } catch (err) {
    hideProgress(decProgress);
    showError(decResult, decResultTitle, decResultBody,
      "Unexpected error", escHtml(err.message));
  } finally {
    decBtn.disabled = false;
  }
});

// ── Hex decoder ────────────────────────────────────────────────────────────

const hexBtn         = document.getElementById("hexBtn");
const hexInput       = document.getElementById("hexInput");
const hexResult      = document.getElementById("hexResult");
const hexResultTitle = document.getElementById("hexResultTitle");
const hexResultBody  = document.getElementById("hexResultBody");

hexBtn.addEventListener("click", async () => {
  const val = hexInput.value.trim();
  if (!val) return;

  hideResult(hexResult);
  hexBtn.disabled = true;

  try {
    const json = await window.api.decodeHex(val);

    if (json.success && json.username) {
      showSuccess(hexResult, hexResultTitle, hexResultBody,
        "ID decoded",
        `Username: <span class="username-badge">${escHtml(json.username)}</span>`);
    } else {
      showError(hexResult, hexResultTitle, hexResultBody,
        "Decode failed",
        escHtml(json.error || "Unknown error"));
    }
  } catch (err) {
    showError(hexResult, hexResultTitle, hexResultBody,
      "Unexpected error", escHtml(err.message));
  } finally {
    hexBtn.disabled = false;
  }
});

// Allow Enter key in the hex input field
hexInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") hexBtn.click();
});

// ── utility ────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
