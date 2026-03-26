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
 *   fileInput  HTMLInputElement  (may have `multiple`)
 *   labelEl    HTMLElement  (shows chosen filename(s))
 *   button     HTMLButtonElement
 *   onFile     (File|File[]) => void  — array when fileInput has `multiple`
 */
function setupDropZone({ dropZone, fileInput, labelEl, button, onFile }) {
  const multi = fileInput.multiple;

  function handleFiles(fileList) {
    const files = Array.from(fileList).filter((f) => {
      const ext = f.name.split(".").pop().toLowerCase();
      return ["mp4", "mov"].includes(ext);
    });
    if (!files.length) {
      labelEl.textContent = "⚠ Only .mp4 and .mov are accepted";
      button.disabled = true;
      return;
    }
    labelEl.textContent = files.length === 1
      ? files[0].name
      : `${files.length} files selected`;
    button.disabled = false;
    onFile(multi ? files : files[0]);
  }

  dropZone.addEventListener("click",  () => fileInput.click());
  dropZone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) handleFiles(fileInput.files);
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
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
const visibleToggle    = document.getElementById("visibleToggle");

let selectedFiles = [];

setupDropZone({
  dropZone:  document.getElementById("encDropZone"),
  fileInput: document.getElementById("encFileInput"),
  labelEl:   document.getElementById("encFilename"),
  button:    encBtn,
  onFile:    (files) => { selectedFiles = files; updateEncBtn(); },
});

encUsername.addEventListener("input", updateEncBtn);

function updateEncBtn() {
  encBtn.disabled = !(selectedFiles.length > 0 && encUsername.value.trim().length > 0);
}

encBtn.addEventListener("click", async () => {
  if (!selectedFiles.length || !encUsername.value.trim()) return;

  hideResult(encResult);
  encResultActions.innerHTML = "";
  encBtn.disabled = true;

  const username   = encUsername.value.trim();
  const addVisible = visibleToggle.checked;
  const total      = selectedFiles.length;
  const saved      = [];
  const failed     = [];

  for (let i = 0; i < total; i++) {
    const file = selectedFiles[i];
    encProgressLabel.textContent = `Processing ${i + 1} of ${total}: ${file.name}…`;
    showProgress(encProgress);

    try {
      const formData = new FormData();
      formData.append("video",       file, file.name);
      formData.append("username",    username);
      formData.append("add_visible", String(addVisible));

      const res  = await fetch(`${API_BASE}/encode`, { method: "POST", body: formData });
      const json = await res.json();

      if (!res.ok || !json.success) {
        failed.push(`${file.name}: ${json.detail || json.error || "Unknown error"}`);
        continue;
      }

      encProgressLabel.textContent = `Saving ${i + 1} of ${total}: ${file.name}…`;

      const dlRes = await fetch(`${API_BASE}/download/${json.download_token}`);
      if (!dlRes.ok) { failed.push(`${file.name}: download HTTP ${dlRes.status}`); continue; }

      const blob     = await dlRes.blob();
      const arrayBuf = await blob.arrayBuffer();
      const outName  = file.name.replace(/\.[^.]+$/, "") + "_watermarked.mp4";
      const saveRes  = await window.api.saveFile({ filename: outName, buffer: arrayBuf });

      if (saveRes.canceled) {
        failed.push(`${file.name}: save dialog dismissed`);
      } else if (!saveRes.success) {
        failed.push(`${file.name}: ${saveRes.error || "save failed"}`);
      } else {
        saved.push(saveRes.filePath);
      }

    } catch (err) {
      failed.push(`${file.name}: ${err.message}`);
    }
  }

  hideProgress(encProgress);
  fetch(`${API_BASE}/cleanup`, { method: "POST" }).catch(() => {});

  if (failed.length === 0) {
    showSuccess(encResult, encResultTitle, encResultBody,
      `Successfully processed ${saved.length} file${saved.length !== 1 ? "s" : ""}!`,
      saved.map((p) => `✓ ${escHtml(p)}`).join("\n"));
  } else {
    const bodyLines = [
      saved.length  ? `✓ ${saved.length} saved:\n${saved.map((p) => `  ${escHtml(p)}`).join("\n")}` : "",
      failed.length ? `✗ ${failed.length} failed:\n${failed.map((m) => `  ${escHtml(m)}`).join("\n")}` : "",
    ].filter(Boolean).join("\n\n");
    showError(encResult, encResultTitle, encResultBody,
      `${failed.length} of ${total} failed`, bodyLines);
  }

  updateEncBtn();
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
