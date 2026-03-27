"""
Steganographic video watermark encoder.

Interface:
    encode_video(input_path, username, output_path, password=42) -> dict
    Returns: {"success": bool, "output_path": str, "error": str | None, "frames_processed": int}

Design notes:
- blind_watermark mode='str' uses bin(int(hex))[2:] which silently drops leading
  zeros from the first byte (e.g. 'D'=0x44 starts with 0-bit).  This causes a
  systematic 1-bit shift and garbled output.  We bypass this by using mode='bit'
  with numpy.unpackbits / packbits, which guarantees exactly MAX_PAYLOAD_LEN*8
  bits regardless of byte values.
- OpenCV VideoWriter mp4v / H.264 compression destroys DCT watermarks.  We use
  MJPG (per-frame JPEG at quality 95) for the intermediate file; blind_watermark
  is explicitly designed to survive JPEG compression.  When FFmpeg is available
  the final output is re-encoded to H.264 crf-17 (high quality); without FFmpeg
  the MJPG AVI is copied directly (watermark intact, watchable).
"""

import base64
import cv2
import imageio_ffmpeg
import numpy as np
import os
import sys
import shutil
import subprocess
import tempfile

# stdin=DEVNULL prevents [Errno 22] when spawned by a GUI host (Electron)
# whose stdin handle is null/closed.  CREATE_NO_WINDOW is Windows-only.
_SP: dict = {"stdin": subprocess.DEVNULL}
if sys.platform == "win32":
    _SP["creationflags"] = subprocess.CREATE_NO_WINDOW

from blind_watermark import WaterMark

# Fixed payload length in BYTES.  Decoder uses the same constant so wm_shape
# is always MAX_PAYLOAD_LEN * 8 bits — no metadata needed.
MAX_PAYLOAD_LEN = 64


def encode_video(
    input_path: str,
    username: str,
    output_path: str,
    password: int = 42,
) -> dict:
    """Embed stego watermark and finalise to output_path.

    With FFmpeg: MJPG AVI → H.264 crf-17 MP4 (+ audio mux if source has audio).
    Without FFmpeg: MJPG AVI copied directly.
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="wm_enc_")
        mjpg_path = os.path.join(tmp_dir, "stego.avi")

        result = _write_mjpg(input_path, username, mjpg_path, password, tmp_dir)
        if not result["success"]:
            return result

        audio_path = result.pop("_audio_path")
        has_audio = result.pop("_has_audio")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        if has_audio:
            _mux_audio(mjpg_path, audio_path, output_path)
        elif _ffmpeg_available():
            _reencode(mjpg_path, output_path)
        else:
            shutil.copy2(mjpg_path, output_path)

        result["output_path"] = output_path
        return result

    except Exception as exc:
        return {
            "success": False,
            "output_path": output_path,
            "error": str(exc),
            "frames_processed": 0,
        }

    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _encode_to_mjpg(
    input_path: str,
    username: str,
    output_avi_path: str,
    password: int = 42,
) -> dict:
    """Embed stego watermark and write a MJPG AVI — no FFmpeg H.264 re-encode.

    Used by full_watermark so the H.264 compression happens only once (during
    the overlay pass), preserving the stego signal through only a single lossy
    encode rather than two.
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="wm_mjpg_")
        result = _write_mjpg(input_path, username, output_avi_path, password, tmp_dir)
        if result["success"]:
            result.pop("_audio_path", None)
            result.pop("_has_audio", None)
            result["output_path"] = output_avi_path
        return result
    except Exception as exc:
        return {
            "success": False,
            "output_path": output_avi_path,
            "error": str(exc),
            "frames_processed": 0,
        }
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_mjpg(
    input_path: str,
    username: str,
    mjpg_path: str,
    password: int,
    tmp_dir: str,
) -> dict:
    """Core frame-processing loop → MJPG AVI.  Returns result dict with
    _audio_path and _has_audio keys appended for use by callers."""
    if not os.path.exists(input_path):
        return {
            "success": False,
            "output_path": mjpg_path,
            "error": f"Input file not found: {input_path}",
            "frames_processed": 0,
            "_audio_path": None,
            "_has_audio": False,
        }

    # ── extract audio ────────────────────────────────────────────────────────
    audio_path = os.path.join(tmp_dir, "audio.aac")
    has_audio = _extract_audio(input_path, audio_path)

    # ── open video ───────────────────────────────────────────────────────────
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        return {
            "success": False,
            "output_path": mjpg_path,
            "error": "Cannot open input video",
            "frames_processed": 0,
            "_audio_path": audio_path,
            "_has_audio": False,
        }

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    # Cap at 5 watermarked frames: the decoder reads only frame 0, so 5 frames
    # gives redundancy without the per-frame blind_watermark cost for long/large videos.
    wm_frame_count = min(max(1, int(total_frames * 0.05)), 5)

    wm_bits = _text_to_bits(f"DITZY:{_encode_username(username)}")

    # ── set up MJPG writer ───────────────────────────────────────────────────
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    out = cv2.VideoWriter(mjpg_path, fourcc, fps, (width, height))
    out.set(cv2.VIDEOWRITER_PROP_QUALITY, 95)
    if not out.isOpened():
        cap.release()
        return {
            "success": False,
            "output_path": mjpg_path,
            "error": "Cannot initialise VideoWriter",
            "frames_processed": 0,
            "_audio_path": audio_path,
            "_has_audio": has_audio,
        }

    frame_idx = 0
    frames_processed = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx < wm_frame_count:
            fin  = os.path.join(tmp_dir, f"fin_{frame_idx}.png")
            fout = os.path.join(tmp_dir, f"fout_{frame_idx}.png")
            cv2.imwrite(fin, frame)

            bwm = WaterMark(password_img=password, password_wm=password)
            # Increase robustness (d1/d2) so the stego signal survives the
            # subsequent H.264 encode in overlay.add_visible_watermark.
            bwm.bwm_core.d1 = 70
            bwm.bwm_core.d2 = 45
            bwm.read_img(fin)
            bwm.read_wm(wm_bits, mode="bit")
            bwm.embed(fout)

            wm_frame = cv2.imread(fout)
            if wm_frame is not None and wm_frame.shape == frame.shape:
                frame = wm_frame
            frames_processed += 1

        out.write(frame)
        frame_idx += 1

    cap.release()
    out.release()

    return {
        "success": True,
        "output_path": mjpg_path,
        "error": None,
        "frames_processed": frames_processed,
        "_audio_path": audio_path,
        "_has_audio": has_audio,
    }


# ── bit helpers ───────────────────────────────────────────────────────────────

def _encode_username(username: str) -> str:
    """URL-safe base64-encode *username* (no padding) so the payload contains
    only alphanumeric + '-_' characters.

    This ensures that:
    - Spaces and special characters in usernames are preserved exactly.
    - Trailing-space stripping in the decoder (rstrip) cannot truncate the value.
    - The embedded payload remains pure ASCII regardless of input encoding.

    Max safe original username: ~40 UTF-8 bytes (base64 expands to ≤54 chars;
    combined with the 6-byte "DITZY:" prefix this fits within MAX_PAYLOAD_LEN=64).
    """
    return base64.urlsafe_b64encode(username.encode("utf-8")).rstrip(b"=").decode()


def _decode_username(encoded: str) -> str:
    """Inverse of _encode_username. Tolerates missing base64 padding."""
    # Restore standard base64 padding (length must be a multiple of 4)
    padding = (4 - len(encoded) % 4) % 4
    return base64.urlsafe_b64decode(encoded + "=" * padding).decode("utf-8", errors="replace")


def _text_to_bits(text: str) -> np.ndarray:
    """Encode *text* as exactly MAX_PAYLOAD_LEN*8 bits (numpy bool array).

    The string is space-padded / truncated to MAX_PAYLOAD_LEN bytes, then
    numpy.unpackbits gives an exact bit representation with no leading-zero
    loss — unlike bin(int(hex))[2:] used by blind_watermark's own str mode.
    """
    padded = text.encode("utf-8")[:MAX_PAYLOAD_LEN].ljust(MAX_PAYLOAD_LEN, b" ")
    return np.unpackbits(np.frombuffer(padded, dtype=np.uint8)).astype(bool)


# ── FFmpeg location ───────────────────────────────────────────────────────────

def _ffmpeg_exe() -> str:
    """Return the ffmpeg executable path (bundled via imageio-ffmpeg)."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def _ffmpeg_available() -> bool:
    try:
        subprocess.run([_ffmpeg_exe(), "-version"], capture_output=True, timeout=10, **_SP)
        return True
    except Exception:
        return False


# ── audio / FFmpeg helpers ────────────────────────────────────────────────────

def _extract_audio(input_path: str, audio_path: str) -> bool:
    """Extract audio to *audio_path*.  Returns True if a non-empty track was saved."""
    try:
        result = subprocess.run(
            [
                _ffmpeg_exe(), "-y", "-i", input_path,
                "-vn", "-acodec", "aac", "-b:a", "192k",
                audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=300,
            **_SP,
        )
        return (
            result.returncode == 0
            and os.path.exists(audio_path)
            and os.path.getsize(audio_path) > 0
        )
    except Exception:
        return False


def _reencode(src: str, dst: str) -> None:
    """Re-encode MJPG AVI → H.264 MP4 at high quality (no audio)."""
    result = subprocess.run(
        [
            _ffmpeg_exe(), "-y", "-i", src,
            "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p",
            dst,
        ],
        capture_output=True,
        text=True,
        timeout=600,
        **_SP,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg re-encode failed: {result.stderr[-500:]}")


def _mux_audio(video_path: str, audio_path: str, output_path: str) -> None:
    """Re-encode MJPG AVI + audio → final H.264 MP4."""
    result = subprocess.run(
        [
            _ffmpeg_exe(), "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            output_path,
        ],
        capture_output=True,
        text=True,
        timeout=600,
        **_SP,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg mux failed: {result.stderr[-500:]}")
