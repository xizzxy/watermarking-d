"""
Tests for the steganographic encoder/decoder pipeline.
"""

import os
import sys

import cv2
import numpy as np
import pytest

# Ensure python/ is on sys.path regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from encoder import encode_video
from decoder import decode_video


# ── helpers ───────────────────────────────────────────────────────────────────

def _gradient_frame(seed: int, height: int, width: int) -> np.ndarray:
    """Return a natural-looking gradient frame with mild noise.

    Pure random noise is the worst case for DCT-based watermarking (low energy
    in low-frequency components).  Gradient frames mimic natural video content
    and ensure the watermark survives JPEG/MJPG compression.
    """
    rng = np.random.default_rng(seed)
    frame = np.zeros((height, width, 3), dtype=np.int16)
    for ch in range(3):
        lo, hi = int(rng.integers(0, 100)), int(rng.integers(155, 255))
        xs = np.linspace(lo, hi, width)
        ys = np.linspace(hi, lo, height)
        frame[:, :, ch] = (ys[:, np.newaxis] + xs[np.newaxis, :]) // 2
    # Add mild texture noise
    frame += rng.integers(-15, 16, (height, width, 3), dtype=np.int16)
    return np.clip(frame, 0, 255).astype(np.uint8)


def make_test_video(
    path: str,
    num_frames: int = 10,
    width: int = 640,
    height: int = 480,
    fps: float = 30.0,
) -> None:
    """Write a synthetic MP4 with gradient frames (good for stego quality)."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
    for i in range(num_frames):
        writer.write(_gradient_frame(i, height, width))
    writer.release()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_encode_success(tmp_path):
    """encode_video should return success=True and create the output file."""
    src = str(tmp_path / "input.mp4")
    dst = str(tmp_path / "output.mp4")
    make_test_video(src)

    result = encode_video(src, "test_fan_123", dst)

    assert result["success"] is True, f"Encode failed: {result['error']}"
    assert os.path.exists(dst), "Output file was not created"
    assert result["frames_processed"] >= 1


def test_decode_roundtrip(tmp_path):
    """Decoded username should match the one used during encoding."""
    src = str(tmp_path / "input.mp4")
    dst = str(tmp_path / "output.mp4")
    make_test_video(src)

    enc = encode_video(src, "test_fan_123", dst)
    assert enc["success"] is True, f"Encode failed: {enc['error']}"

    dec = decode_video(dst)
    assert dec["success"] is True, f"Decode failed: {dec['error']}"
    assert dec["username"] == "test_fan_123", (
        f"Expected 'test_fan_123', got {dec['username']!r}  raw={dec['raw_payload']!r}"
    )


def test_encode_nonexistent_file(tmp_path):
    """encode_video should return success=False for a missing input."""
    result = encode_video(
        str(tmp_path / "does_not_exist.mp4"),
        "user",
        str(tmp_path / "out.mp4"),
    )
    assert result["success"] is False
    assert result["error"] is not None
