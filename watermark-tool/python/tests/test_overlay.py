"""
Tests for overlay.py — visible watermark and full pipeline.
"""

import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from overlay import add_visible_watermark, full_watermark, generate_dynamic_watermark
from decoder import decode_video


# ── shared helpers ────────────────────────────────────────────────────────────

def _gradient_frame(seed: int, height: int, width: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    frame = np.zeros((height, width, 3), dtype=np.int16)
    for ch in range(3):
        lo = int(rng.integers(0, 100))
        hi = int(rng.integers(155, 255))
        xs = np.linspace(lo, hi, width)
        ys = np.linspace(hi, lo, height)
        frame[:, :, ch] = (ys[:, np.newaxis] + xs[np.newaxis, :]) // 2
    frame += rng.integers(-15, 16, (height, width, 3), dtype=np.int16)
    return np.clip(frame, 0, 255).astype(np.uint8)


def make_test_video(
    path: str,
    num_frames: int = 10,
    width: int = 640,
    height: int = 480,
    fps: float = 30.0,
) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
    for i in range(num_frames):
        writer.write(_gradient_frame(i, height, width))
    writer.release()


@pytest.fixture()
def logo_path(tmp_path):
    """200x60 RGBA logo PNG used by all overlay tests."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (200, 60), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), "DITZY.AI")
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((200 - w) // 2, (60 - h) // 2), "DITZY.AI", fill=(64, 64, 64, 255))
    p = str(tmp_path / "logo.png")
    img.save(p)
    return p


# ── tests ─────────────────────────────────────────────────────────────────────

def test_add_visible_watermark(tmp_path, logo_path):
    """add_visible_watermark should produce a non-empty output file."""
    src = str(tmp_path / "input.mp4")
    dst = str(tmp_path / "output.mp4")
    make_test_video(src)

    result = add_visible_watermark(src, dst, logo_path)

    assert result["success"] is True, f"Overlay failed: {result['error']}"
    assert os.path.exists(dst), "Output file was not created"
    assert os.path.getsize(dst) > 0, "Output file is empty"


def test_full_watermark_roundtrip(tmp_path):
    """full_watermark should produce watchable output AND preserve stego username."""
    src = str(tmp_path / "input.mp4")
    dst = str(tmp_path / "output.mp4")
    make_test_video(src)

    result = full_watermark(src, "test_fan_123", dst)

    assert result["success"] is True, f"full_watermark failed: {result['error']}"
    assert os.path.exists(dst), "Output file was not created"
    assert os.path.getsize(dst) > 0, "Output file is empty"

    dec = decode_video(dst)
    assert dec["success"] is True, f"Decode failed: {dec['error']}"
    assert dec["username"] == "test_fan_123", (
        f"Expected 'test_fan_123', got {dec['username']!r}  raw={dec['raw_payload']!r}"
    )


# ── generate_dynamic_watermark tests ─────────────────────────────────────────

def test_generate_dynamic_watermark_creates_png():
    """generate_dynamic_watermark should create a valid RGBA PNG file."""
    from PIL import Image
    path = generate_dynamic_watermark("testuser")
    try:
        assert os.path.exists(path), "PNG file was not created"
        assert os.path.getsize(path) > 0, "PNG file is empty"
        with Image.open(path) as img:   # context manager closes handle before finally
            assert img.mode == "RGBA", f"Expected RGBA mode, got {img.mode}"
            w, h = img.size
            assert w > 0 and h > 0, f"Unexpected image dimensions: {img.size}"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_generate_dynamic_watermark_hex_content():
    """PNG should contain non-transparent pixels encoding the username as hex."""
    from PIL import Image
    username = "hello"
    path = generate_dynamic_watermark(username)
    try:
        img = Image.open(path)
        data = list(img.getdata())
        non_transparent = sum(1 for px in data if px[3] > 0)
        assert non_transparent > 0, "Expected non-transparent pixels for text, found none"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_generate_dynamic_watermark_special_chars():
    """generate_dynamic_watermark should handle special characters and unicode."""
    from PIL import Image
    for username in ["superfan@99", "用户名", "trailing space "]:
        path = generate_dynamic_watermark(username)
        try:
            assert os.path.exists(path), f"PNG not created for username {username!r}"
            with Image.open(path) as img:   # context manager closes handle before finally
                w, h = img.size
                assert w > 0 and h > 0, f"Zero-size image for {username!r}"
        finally:
            if os.path.exists(path):
                os.remove(path)


def test_full_watermark_uses_dynamic_png_no_logo(tmp_path):
    """full_watermark should succeed even when logo_path is empty string."""
    src = str(tmp_path / "input.mp4")
    dst = str(tmp_path / "output.mp4")
    make_test_video(src)

    # Pass no logo_path (default) — should still produce output via dynamic PNG
    result = full_watermark(src, "no_logo_fan", dst)

    assert result["success"] is True, f"full_watermark failed without logo: {result['error']}"
    assert os.path.exists(dst)
    assert os.path.getsize(dst) > 0

    dec = decode_video(dst)
    assert dec["success"] is True
    assert dec["username"] == "no_logo_fan"
