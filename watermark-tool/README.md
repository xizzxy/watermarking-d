# Watermark Tool

A desktop application for embedding and decoding per-fan steganographic watermarks in videos before distribution via Inflow.  When a watermarked clip leaks, the tool extracts the embedded watermark and identifies which fan received that copy.

---

## What it does

| Feature | Description |
|---|---|
| **Steganographic watermark** | Embeds a fan's username into the DCT frequency domain of the first few video frames using [blind-watermark](https://github.com/guofei9987/blind_watermark). Invisible to the naked eye. |
| **Visible watermark** | Burns a semi-transparent logo overlay onto every frame using FFmpeg. Provides a fallback for screen recordings. |
| **Decode tab** | Extracts the hidden username from a suspected leaked video in seconds. |
| **Drag-and-drop UI** | Native Electron desktop app — no browser or cloud upload required. |
| **Self-contained** | Python backend compiled with PyInstaller. End users do not need Python installed. |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **FFmpeg** | Must be on PATH. Install: `winget install ffmpeg` (Windows) or `brew install ffmpeg` (macOS) |
| **Node.js 18+** | For Electron dev mode and building |
| **Python 3.14+** | Dev mode only — not required for packaged app end users |

---

## Setup — development mode

```bash
# 1. Clone
git clone <repo-url>
cd watermark-tool

# 2. Python backend
cd python
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
cd ..

# 3. Electron frontend
cd electron
npm install
```

### Run in dev mode

Electron automatically spawns the Python venv backend on startup:

```bash
cd electron
npm start
```

The backend status dot in the top-right corner of the app turns green once the Python server is ready.

### Run the test suite

```bash
cd python
venv\Scripts\python -m pytest tests/ -v
```

17 tests pass.  The full suite takes ~2–3 minutes (includes a 1080p performance benchmark and 20-video survival test).

---

## How to build

### Step 1 — compile the Python backend with PyInstaller

The Python backend must be compiled into a standalone executable **on the target platform** before Electron-builder packaging.  PyInstaller bundles the Python interpreter, all dependencies (blind-watermark, OpenCV, FastAPI, uvicorn, numpy, etc.), and the server code into a single binary.

```bash
cd python
venv\Scripts\pyinstaller.exe --clean --distpath dist --workpath build server.spec
# macOS / Linux:
# venv/bin/pyinstaller --clean --distpath dist --workpath build server.spec
```

Output:
- Windows → `python/dist/server.exe` (~74 MB)
- macOS / Linux → `python/dist/server`

> **Platform limitation — PyInstaller is NOT cross-compilable.**
> The `server.exe` produced on Windows will only run on Windows.
> A macOS `server` binary must be built on macOS, and Linux on Linux.
> For multi-platform releases, use a CI matrix (e.g. GitHub Actions with
> `windows-latest`, `macos-latest`, and `ubuntu-latest` runners), build the
> backend on each, then package with Electron-builder on the same runner.

### Step 2 — package with Electron-builder

```bash
cd electron
npm run dist
```

`electron-builder` reads the `build` section in `package.json` and:
- Bundles `python/dist/server.exe` (or `server`) under `resources/python/dist/`
- Bundles `assets/` under `resources/assets/`
- Produces a platform-native installer

| Platform | Output file | Location |
|---|---|---|
| Windows | `Watermark Tool Setup 0.1.0.exe` (NSIS) | `electron/dist-electron/` |
| macOS | `Watermark Tool-0.1.0.dmg` | `electron/dist-electron/` |
| Linux | `Watermark Tool-0.1.0.AppImage` | `electron/dist-electron/` |

> **Platform limitation — Electron-builder is also NOT cross-compilable.**
> Build the `.exe` installer on Windows, the `.dmg` on macOS, and the
> `.AppImage` on Linux.  macOS builds additionally require Xcode Command Line
> Tools (`xcode-select --install`).

---

## How to use the app

### Encode tab — watermark a video for a fan

1. Drag and drop a `.mp4` or `.mov` file onto the drop zone (or click to browse).
2. Enter the fan's **username** in the text field.
3. Click **Embed Watermark**.
4. A native Save dialog opens — choose where to save the watermarked `.mp4`.
5. Send that file to the fan via Inflow.

### Decode tab — identify a fan from a leaked video

1. Drag and drop the suspected leaked `.mp4` or `.mov` onto the drop zone.
2. Click **Identify Fan**.
3. The fan's username is shown, or "No watermark found" if the video was not watermarked by this tool.

---

## Technical notes

### Stego algorithm

- DWT + DCT domain embedding via [blind-watermark 0.4.4](https://github.com/guofei9987/blind_watermark).
- Usernames are **base64url-encoded** before embedding — spaces, special characters, and Unicode are all preserved through the round-trip.
- Payload: `DITZY:{base64url_username}`, space-padded to a fixed 64 bytes, embedded as exactly 512 bits using `mode='bit'` (avoids the leading-zero truncation bug in blind_watermark's `mode='str'`).
- Embedding strength: `d1=70`, `d2=45`.  Survives H.264 CRF 18–23 reliably.
- Only the first `min(max(1, 5% of frames), 5)` frames are watermarked; the decoder reads only frame 0.

### Watermark survival rates (QA results)

| Scenario | Result |
|---|---|
| Single H.264 encode CRF 18 | 100% (20/20) |
| Single H.264 encode CRF 20 | 100% (20/20) |
| Single H.264 encode CRF 23 | 100% (20/20) |
| False positive rate (unwatermarked video) | 0% (0/10) |
| 1080p 300-frame encode time | < 120 s |

**Known limitation — double H.264 encode:** if the recipient re-encodes the distributed video before leaking (e.g. social media upload at CRF 28+), the stego signal is likely lost.  Increasing `d1`/`d2` beyond 70/45 improves double-encode robustness at the cost of visible distortion on the steganographic frames.

### FFmpeg

Required for visible overlay (`filter_complex`), H.264 re-encoding, and audio muxing.  The app resolves FFmpeg via `shutil.which("ffmpeg")` then falls back to known Windows install paths.
