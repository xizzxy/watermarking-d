"""
Steganographic video watermark decoder.

Interface:
    decode_video(video_path, password=42) -> dict
    Returns: {"success": bool, "username": str | None, "raw_payload": str | None, "error": str | None}
"""

import base64
import cv2
import numpy as np
import os
import shutil
import tempfile

from blind_watermark import WaterMark

MAX_PAYLOAD_LEN = 64  # must match encoder


def decode_video(video_path: str, password: int = 42) -> dict:
    tmp_dir = None
    try:
        if not os.path.exists(video_path):
            return {
                "success": False,
                "username": None,
                "raw_payload": None,
                "error": f"File not found: {video_path}",
            }

        tmp_dir = tempfile.mkdtemp(prefix="wm_dec_")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {
                "success": False,
                "username": None,
                "raw_payload": None,
                "error": "Cannot open video",
            }

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

        # Spread samples evenly across the actual duration so that any trimmed
        # sub-clip is scanned proportionally rather than at fixed offsets.
        WM_SCAN_MAX = 20

        wm_shape  = MAX_PAYLOAD_LEN * 8  # bits
        frame_png = os.path.join(tmp_dir, "frame.png")

        last_payload = None
        found = False

        for attempt in range(WM_SCAN_MAX):
            seek_frame = int(attempt * total_frames / WM_SCAN_MAX)
            if seek_frame >= total_frames:
                break

            cap.set(cv2.CAP_PROP_POS_FRAMES, seek_frame)
            ret, frame = cap.read()
            if not ret:
                continue

            cv2.imwrite(frame_png, frame)

            bwm = WaterMark(password_img=password, password_wm=password)
            # Match the increased d1/d2 set in encoder._write_mjpg
            bwm.bwm_core.d1 = 36
            bwm.bwm_core.d2 = 20

            # mode='bit' returns a boolean numpy array — avoids the leading-zero
            # truncation bug in blind_watermark's own str mode.
            bits = bwm.extract(frame_png, wm_shape=wm_shape, mode="bit")
            payload = _bits_to_text(bits)
            last_payload = payload

            if payload.startswith("DITZY:"):
                found = True
                break

        cap.release()

        if found:
            encoded = last_payload[len("DITZY:"):].rstrip()
            username = _decode_username(encoded)
            return {
                "success": True,
                "username": username,
                "raw_payload": last_payload,
                "error": None,
            }

        if last_payload is not None:
            return {
                "success": True,
                "username": None,
                "raw_payload": last_payload,
                "error": None,
            }

        return {
            "success": False,
            "username": None,
            "raw_payload": None,
            "error": "Cannot read any frame from video",
        }

    except Exception as exc:
        return {
            "success": False,
            "username": None,
            "raw_payload": None,
            "error": str(exc),
        }

    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── username helpers ──────────────────────────────────────────────────────────

_B64URL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _decode_username(encoded: str) -> str:
    """Decode a URL-safe base64-encoded username produced by encoder._encode_username.

    Tolerates missing base64 padding and strips any non-base64 characters
    (null bytes, Unicode replacement chars) that DCT extraction noise may have
    appended to the payload after the valid base64 content.
    """
    clean = "".join(c for c in encoded if c in _B64URL_CHARS)
    if not clean:
        return encoded
    padding = (4 - len(clean) % 4) % 4
    try:
        return base64.urlsafe_b64decode(clean + "=" * padding).decode("utf-8", errors="replace")
    except Exception:
        return encoded


# ── bit helper ────────────────────────────────────────────────────────────────

def _bits_to_text(bits) -> str:
    """Convert a boolean/float bit array of length MAX_PAYLOAD_LEN*8 to text.

    numpy.packbits is the exact inverse of numpy.unpackbits used in the encoder,
    ensuring correct reconstruction of every bit including leading zeros.
    """
    binary = np.array(bits > 0.5, dtype=np.uint8)
    # Truncate to a multiple of 8 just in case of rounding
    n_bits = (len(binary) // 8) * 8
    packed = np.packbits(binary[:n_bits])
    return packed.tobytes().decode("utf-8", errors="replace").rstrip()
