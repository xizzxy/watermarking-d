/**
 * Preload script — runs in a privileged context with access to Node APIs
 * but exposes only a narrow, typed surface to the renderer via contextBridge.
 */

"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  /**
   * Ask the main process to show a Save dialog and write the file to disk.
   * @param {{ filename: string, buffer: ArrayBuffer }} opts
   * @returns {Promise<{ success: boolean, filePath?: string, canceled?: boolean, error?: string }>}
   */
  saveFile: (opts) => ipcRenderer.invoke("save-file", opts),

  /**
   * Get the FastAPI base URL (e.g. "http://127.0.0.1:8765").
   * @returns {Promise<string>}
   */
  getApiBase: () => ipcRenderer.invoke("get-api-base"),

  /**
   * Decode a visible DITZY-ID hex string to the original username.
   * Accepts raw hex or the full "DITZY-ID:AABB..." form.
   * @param {string} hexId
   * @returns {Promise<{ success: boolean, username: string|null, error: string|null }>}
   */
  decodeHex: (hexId) => ipcRenderer.invoke("decode-hex", hexId),
});
