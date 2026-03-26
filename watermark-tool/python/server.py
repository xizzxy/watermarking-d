"""
FastAPI IPC server for Watermark Tool.

Runs on 127.0.0.1:8765.  Electron spawns this process on app start and
communicates exclusively over localhost; no external network exposure intended.
"""

import os
import sys
import time
import uuid
from pathlib import Path

import traceback
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

# ── import sibling modules regardless of cwd ─────────────────────────────────
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from overlay import full_watermark   # noqa: E402
from decoder import decode_video     # noqa: E402

# ── paths ─────────────────────────────────────────────────────────────────────
_TEMP_DIR = _HERE / "temp"

# ── constants ─────────────────────────────────────────────────────────────────
_MAX_UPLOAD_BYTES = 2 * 1024 ** 3   # 2 GB
_ALLOWED_EXTS     = {".mp4", ".mov"}
_VERSION          = "0.1.2-TRACE"

# ── desktop crash log ─────────────────────────────────────────────────────────
_DESKTOP_LOG = os.path.join(os.path.expanduser("~"), "Desktop", "DITZY_CRASH_LOG.txt")

def _write_log(msg: str) -> None:
    try:
        with open(_DESKTOP_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

# ── in-memory token store  (uuid → absolute file path) ───────────────────────
_tokens: dict[str, str] = {}

# ── app ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(application: FastAPI):   # noqa: ARG001
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Watermark Tool API", version=_VERSION, lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    # Electron renderer loads from file://, browsers send origin "null" for
    # file:// pages; include both.
    allow_origins=["http://localhost", "file://", "null"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _ensure_temp() -> None:
    """Ensure temp dir exists — called at the start of each upload endpoint
    so the server works even when startup event hasn't fired (e.g. TestClient
    without lifespan context)."""
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)


def _tmp(prefix: str, suffix: str) -> str:
    return str(_TEMP_DIR / f"{prefix}_{uuid.uuid4().hex}{suffix}")


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": _VERSION}


@app.post("/encode")
async def encode(
    video: UploadFile = File(...),
    username: str = Form(...),
) -> JSONResponse:
    """Embed stego + visible watermark.

    Request  : multipart/form-data  { video: <file>, username: <str> }
    Response : { success: bool, download_token: str | null, error: str | null }
    """
    # ── input validation ─────────────────────────────────────────────────────
    if not username or not username.strip():
        raise HTTPException(status_code=400, detail="username must not be empty")

    ext = Path(video.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or '(none)'}'. Accepted: .mp4 .mov",
        )

    _ensure_temp()
    input_path  = _tmp("in",  ext)
    output_path = _tmp("out", ".mp4")

    _write_log(f"\n{'='*60}\n/encode called: username={username!r} file={video.filename!r}")

    try:
        content = await video.read()
        if len(content) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds 2 GB limit")

        with open(input_path, "wb") as fh:
            fh.write(content)

        _write_log(f"Upload saved to: {input_path}")

        # Run in a thread so the event loop stays free for /health pings
        # while the CPU-bound stego+FFmpeg work is in progress.
        result = await run_in_threadpool(
            full_watermark, input_path, username.strip(), output_path
        )

        _write_log(f"full_watermark result: {result}")

        if not result["success"]:
            _write_log(f"ENCODE FAILED (result): {result.get('error')}")
            return JSONResponse({
                "success": False,
                "download_token": None,
                "error": result.get("error"),
            })

        token = str(uuid.uuid4())
        _tokens[token] = output_path
        _write_log(f"Encode SUCCESS, token={token}")
        return JSONResponse({"success": True, "download_token": token, "error": None})

    except HTTPException:
        raise   # let FastAPI handle 400 / 413 normally

    except Exception:
        tb = traceback.format_exc()
        _write_log(f"CRASH OCCURRED:\n{tb}")
        return JSONResponse({
            "success": False,
            "download_token": None,
            "error": tb,
        })

    finally:
        # Remove upload immediately; output lives until downloaded or cleaned up
        if os.path.exists(input_path):
            try:
                os.unlink(input_path)
            except OSError:
                pass


@app.post("/decode")
async def decode(video: UploadFile = File(...)) -> JSONResponse:
    """Extract steganographic username from a video.

    Request  : multipart/form-data  { video: <file> }
    Response : { success: bool, username: str | null,
                 raw_payload: str | null, error: str | null }
    """
    _ensure_temp()
    ext = Path(video.filename or "").suffix.lower() or ".mp4"
    input_path = _tmp("dec", ext)

    try:
        content = await video.read()
        if len(content) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds 2 GB limit")

        with open(input_path, "wb") as fh:
            fh.write(content)

        result = decode_video(input_path)
        return JSONResponse(result)

    finally:
        if os.path.exists(input_path):
            try:
                os.unlink(input_path)
            except OSError:
                pass


@app.get("/download/{token}")
async def download(token: str) -> FileResponse:
    """Retrieve a watermarked file by its single-use token.

    The token is removed from memory on first access (single-use).
    The file itself remains on disk until POST /cleanup removes it.
    """
    path = _tokens.get(token)
    if path is None:
        raise HTTPException(status_code=404, detail="Token not found or already used")
    if not os.path.exists(path):
        _tokens.pop(token, None)
        raise HTTPException(status_code=404, detail="Output file missing")

    del _tokens[token]   # single-use

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=Path(path).name,
    )


class _DecodeHexBody(BaseModel):
    hex_id: str


@app.post("/decode-hex")
async def decode_hex(body: _DecodeHexBody) -> JSONResponse:
    """Decode a visible DITZY-ID hex string back to the original username.

    Request  : application/json  { "hex_id": "DITZY-ID:6A6F686E..." }
               (the "DITZY-ID:" prefix and surrounding whitespace are stripped)
    Response : { success: bool, username: str | null, error: str | null }
    """
    raw = body.hex_id.strip()

    # Strip the optional visible prefix that users might paste verbatim
    if raw.upper().startswith("DITZY-ID:"):
        raw = raw[len("DITZY-ID:"):]

    raw = raw.strip()

    if not raw:
        return JSONResponse({"success": False, "username": None,
                             "error": "hex_id is empty after stripping prefix"})

    try:
        username = bytes.fromhex(raw).decode("utf-8")
        return JSONResponse({"success": True, "username": username, "error": None})
    except ValueError as exc:
        return JSONResponse({"success": False, "username": None,
                             "error": f"Invalid hex string: {exc}"})
    except UnicodeDecodeError as exc:
        return JSONResponse({"success": False, "username": None,
                             "error": f"Hex decoded but is not valid UTF-8: {exc}"})


@app.post("/cleanup")
async def cleanup() -> dict:
    """Delete temp files older than 1 hour.

    Response : { success: bool, deleted: int }
    """
    cutoff  = time.time() - 3600
    deleted = 0

    if _TEMP_DIR.exists():
        for entry in _TEMP_DIR.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    deleted += 1
            except OSError:
                pass

    return {"success": True, "deleted": deleted}


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
