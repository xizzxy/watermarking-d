"""
Integration tests for server.py (FastAPI IPC server).

All heavy work (encode + full_watermark pipeline) is done once in a
module-scoped fixture so the suite doesn't run the pipeline 3 times.
"""

import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

# ── path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import app   # noqa: E402


# ── shared video factory (same gradient frames as other tests) ────────────────

def _gradient_frame(seed: int, h: int, w: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    frame = np.zeros((h, w, 3), dtype=np.int16)
    for ch in range(3):
        lo = int(rng.integers(0, 100))
        hi = int(rng.integers(155, 255))
        xs = np.linspace(lo, hi, w)
        ys = np.linspace(hi, lo, h)
        frame[:, :, ch] = (ys[:, np.newaxis] + xs[np.newaxis, :]) // 2
    frame += rng.integers(-15, 16, (h, w, 3), dtype=np.int16)
    return np.clip(frame, 0, 255).astype(np.uint8)


def make_test_video(path: str, n: int = 10, w: int = 640, h: int = 480) -> None:
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (w, h))
    for i in range(n):
        writer.write(_gradient_frame(i, h, w))
    writer.release()


# ── module-scoped pipeline fixture ───────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """TestClient that triggers startup/shutdown lifecycle events."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def src_video(tmp_path_factory) -> str:
    """A small unwatermarked .mp4 used as encode input and as the 'no-wm' file."""
    p = str(tmp_path_factory.mktemp("srv") / "input.mp4")
    make_test_video(p)
    return p


@pytest.fixture(scope="module")
def pipeline(client, src_video, tmp_path_factory):
    """Run POST /encode once and download the result.  Returns a dict with:
      - enc_json  : /encode response body
      - token     : download token (str)
      - encoded   : path to the downloaded encoded .mp4
    """
    tmp = tmp_path_factory.mktemp("dl")

    with open(src_video, "rb") as fh:
        resp = client.post(
            "/encode",
            files={"video": ("input.mp4", fh, "video/mp4")},
            data={"username": "test_fan_123"},
        )

    assert resp.status_code == 200, f"/encode HTTP error: {resp.status_code} {resp.text}"
    enc_json = resp.json()
    assert enc_json.get("success"), f"/encode returned success=False: {enc_json}"

    token = enc_json["download_token"]

    # Download the encoded file
    dl_resp = client.get(f"/download/{token}")
    assert dl_resp.status_code == 200, f"/download HTTP error: {dl_resp.status_code}"

    encoded_path = str(tmp / "encoded.mp4")
    with open(encoded_path, "wb") as fh:
        fh.write(dl_resp.content)

    return {
        "enc_json": enc_json,
        "token": token,
        "encoded": encoded_path,
        "dl_resp": dl_resp,
    }


# ── tests ─────────────────────────────────────────────────────────────────────

def test_health(client):
    """GET /health returns 200 and status=ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_encode_returns_token(pipeline):
    """POST /encode with a valid .mp4 returns success and a non-empty download_token."""
    enc = pipeline["enc_json"]
    assert enc["success"] is True
    assert isinstance(enc["download_token"], str)
    assert len(enc["download_token"]) > 0
    assert enc["error"] is None


def test_download_returns_file(pipeline):
    """GET /download/{token} returns the encoded video file."""
    dl = pipeline["dl_resp"]
    assert dl.status_code == 200
    # Must have non-zero content
    assert len(dl.content) > 0


def test_decode_encoded_file(client, pipeline):
    """POST /decode on the encoded file recovers the original username."""
    encoded = pipeline["encoded"]
    assert os.path.exists(encoded), "Encoded file missing — pipeline fixture failed"

    with open(encoded, "rb") as fh:
        resp = client.post(
            "/decode",
            files={"video": ("encoded.mp4", fh, "video/mp4")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True, f"Decode failed: {data.get('error')}"
    assert data["username"] == "test_fan_123", (
        f"Expected 'test_fan_123', got {data['username']!r}  "
        f"raw={data.get('raw_payload')!r}"
    )


def test_decode_unwatermarked_file(client, src_video):
    """POST /decode on the original (un-watermarked) file returns username=null."""
    with open(src_video, "rb") as fh:
        resp = client.post(
            "/decode",
            files={"video": ("input.mp4", fh, "video/mp4")},
        )

    assert resp.status_code == 200
    data = resp.json()
    # decode_video always returns success=True even when no DITZY: prefix found
    assert data["username"] is None, (
        f"Expected username=null for unwatermarked video, got {data['username']!r}"
    )


# ── /decode-hex tests ─────────────────────────────────────────────────────────

def test_decode_hex_valid(client):
    """POST /decode-hex with valid hex returns the original username."""
    username = "superfan_99"
    hex_id = username.encode("utf-8").hex()
    resp = client.post("/decode-hex", json={"hex_id": hex_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["username"] == username
    assert data["error"] is None


def test_decode_hex_with_ditzy_prefix(client):
    """POST /decode-hex accepts and strips the 'DITZY-ID:' prefix."""
    username = "john"
    hex_id = username.encode("utf-8").hex().upper()
    resp = client.post("/decode-hex", json={"hex_id": f"DITZY-ID:{hex_id}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["username"] == username


def test_decode_hex_uppercase(client):
    """POST /decode-hex works with uppercase hex digits."""
    username = "test"
    hex_id = username.encode("utf-8").hex().upper()
    resp = client.post("/decode-hex", json={"hex_id": hex_id})
    assert resp.status_code == 200
    assert resp.json()["username"] == username


def test_decode_hex_invalid_hex(client):
    """POST /decode-hex returns success=False for non-hex input."""
    resp = client.post("/decode-hex", json={"hex_id": "not-valid-hex!!"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["username"] is None
    assert data["error"] is not None


def test_decode_hex_empty_after_strip(client):
    """POST /decode-hex returns error when hex_id is empty after prefix strip."""
    resp = client.post("/decode-hex", json={"hex_id": "DITZY-ID:"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "empty" in data["error"].lower()


def test_decode_hex_unicode_username(client):
    """POST /decode-hex round-trips a Unicode username."""
    username = "用户名"
    hex_id = username.encode("utf-8").hex()
    resp = client.post("/decode-hex", json={"hex_id": hex_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["username"] == username
