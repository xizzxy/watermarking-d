"""
FFmpeg visible watermark overlay.

Interface:
    generate_dynamic_watermark(username) -> str
    Creates a temp RGBA PNG with "DITZY-ID:{hex}" text; caller must delete it.

    add_visible_watermark(input_path, output_path, logo_path,
                          opacity=0.25, position="bottomright") -> dict
    Returns: {"success": bool, "output_path": str, "error": str | None}

    full_watermark(input_path, username, output_path) -> dict
    Stego-encodes then adds a dynamic visible overlay centered on the frame.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


def _ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


# stdin=DEVNULL prevents [Errno 22] when spawned by Electron (null stdin handle).
# CREATE_NO_WINDOW is Windows-only — guard with platform check.
_SP: dict = {"stdin": subprocess.DEVNULL}
if sys.platform == "win32":
    _SP["creationflags"] = subprocess.CREATE_NO_WINDOW


# ── dynamic watermark image ───────────────────────────────────────────────────

def generate_dynamic_watermark(username: str) -> str:
    """Generate a per-username visible watermark PNG in a temp file.

    The image contains "DITZY-ID:{hex_id}" (UTF-8 bytes of the username
    hex-encoded uppercase) in large white text with a black stroke outline,
    readable on both light and dark video frames.

    The image is auto-sized to the measured text dimensions plus padding so it
    remains crisp before FFmpeg scales it.

    Returns the path to the temp PNG.  **The caller is responsible for deleting
    it** (use os.remove in a finally block).
    """
    hex_id = username.encode("utf-8").hex().upper()
    label  = hex_id

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 28)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except OSError:
            font = ImageFont.load_default(size=28)

    # Measure text on a scratch canvas so we can auto-size the real image.
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = scratch.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad   = 8
    img_w = text_w + pad * 2
    img_h = text_h + pad * 2

    img  = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))   # transparent
    draw = ImageDraw.Draw(img)

    # Offset by bbox origin (can be negative with stroke) + padding
    x = pad - bbox[0]
    y = pad - bbox[1]

    draw.text(
        (x, y), label, font=font,
        fill=(255, 255, 255, 255),
    )

    # Write to a properly-closed temp file (Windows file-lock safe).
    # Close the mkstemp fd FIRST so PIL can open the path without conflict,
    # then save.  On Windows two open handles to the same file causes WinError 32.
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path, format="PNG")
    return path


# ── public API ────────────────────────────────────────────────────────────────

def add_visible_watermark(
    input_path: str,
    output_path: str,
    logo_path: str,
    opacity: float = 0.25,
    position: str = "bottomright",
) -> dict:
    """Burn a semi-transparent logo onto every frame using FFmpeg filter_complex.

    FFmpeg filter chain:
        [1:v] scale=iw*0.15:-1, format=rgba, colorchannelmixer=aa=<opacity> [logo]
        [0:v][logo] overlay=<x>:<y> [out]

    Position tokens: bottomright, bottomleft, topright, topleft, center.
    Output encoding: libx264 crf-18 preset-fast + -c:a copy.
    """
    try:
        if not os.path.exists(input_path):
            return {"success": False, "output_path": output_path,
                    "error": f"Input not found: {input_path}"}
        if not os.path.exists(logo_path):
            return {"success": False, "output_path": output_path,
                    "error": f"Logo not found: {logo_path}"}

        x_expr, y_expr = _position_exprs(position)

        filter_complex = (
            "[1:v]format=rgba,colorchannelmixer=aa=0.35[wm_trans];"
            f"[0:v][wm_trans]overlay={x_expr}:{y_expr}"
        )

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        cmd = [
            _ffmpeg_exe(), "-y",
            "-i", input_path,
            "-i", logo_path,
            "-filter_complex", filter_complex,
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-map", "0:a?",
            "-c:a", "copy",
            output_path,
        ]

        print(f"DIAGNOSTIC FFMPEG CMD: {' '.join(cmd)}", flush=True)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **_SP)

        # Always write a log to the user's Desktop so failures are never silently
        # swallowed inside the packaged .exe.
        try:
            desktop  = os.path.join(os.path.expanduser("~"), "Desktop")
            log_path = os.path.join(desktop, "DITZY_FFMPEG_LOG.txt")
            with open(log_path, "w", encoding="utf-8") as _lf:
                _lf.write(f"CMD:\n{' '.join(cmd)}\n\n")
                _lf.write(f"RETURN CODE: {result.returncode}\n\n")
                _lf.write(f"STDOUT:\n{result.stdout}\n\n")
                _lf.write(f"STDERR:\n{result.stderr}\n")
        except Exception:
            pass   # never let logging crash the encode

        if result.returncode != 0:
            return {
                "success": False,
                "output_path": output_path,
                "error": f"FFmpeg failed (rc={result.returncode}): {result.stderr[-800:]}",
            }

        return {"success": True, "output_path": output_path, "error": None}

    except FileNotFoundError:
        return {"success": False, "output_path": output_path,
                "error": "FFmpeg not found. Install FFmpeg and add it to PATH."}
    except Exception as exc:
        return {"success": False, "output_path": output_path, "error": str(exc)}


def full_watermark(
    input_path: str,
    username: str,
    output_path: str,
    add_visible: bool = True,
) -> dict:
    """Steganographic encode then optionally add a dynamic visible watermark.

    Pipeline (add_visible=True):
        input → [_encode_to_mjpg] → stego.avi
              → [generate_dynamic_watermark] → <uuid>.png
              → [add_visible_watermark(stego.avi, png, center)] → output

    Pipeline (add_visible=False):
        input → [_encode_to_mjpg] → stego.avi
              → FFmpeg passthrough (libx264 crf-18, no overlay) → output
    """
    tmp_dir     = None
    dynamic_png = None
    try:
        from encoder import _encode_to_mjpg  # noqa: PLC0415

        tmp_dir   = tempfile.mkdtemp(prefix="wm_full_")
        stego_tmp = os.path.join(tmp_dir, "stego.avi")

        # Step 1: embed stego watermark → lossless-ish MJPG AVI
        t0 = time.time()
        enc_result = _encode_to_mjpg(input_path, username, stego_tmp)
        print(f"DIAGNOSTIC: Stego encode took {time.time() - t0:.1f}s", flush=True)
        if not enc_result["success"]:
            return {
                "success": False,
                "output_path": output_path,
                "error": f"Stego encode failed: {enc_result['error']}",
            }

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        if add_visible:
            # Step 2: generate per-username visible watermark PNG
            dynamic_png = generate_dynamic_watermark(username)
            print(f"DIAGNOSTIC: PNG generated at {dynamic_png}", flush=True)

            # Step 3: burn overlay → final H.264 output (single FFmpeg pass)
            t1 = time.time()
            ov_result = add_visible_watermark(stego_tmp, output_path, dynamic_png,
                                              position="center")
            print(f"DIAGNOSTIC: FFmpeg overlay took {time.time() - t1:.1f}s", flush=True)
            if not ov_result["success"]:
                return {
                    "success": False,
                    "output_path": output_path,
                    "error": f"Overlay failed: {ov_result['error']}",
                }
        else:
            # No visible overlay — simple H.264 re-encode of the stego AVI
            t1 = time.time()
            cmd = [
                _ffmpeg_exe(), "-y", "-i", stego_tmp,
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-map", "0:a?", "-c:a", "copy",
                output_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **_SP)
            print(f"DIAGNOSTIC: FFmpeg passthrough took {time.time() - t1:.1f}s", flush=True)
            if result.returncode != 0:
                return {
                    "success": False,
                    "output_path": output_path,
                    "error": f"FFmpeg failed (rc={result.returncode}): {result.stderr[-800:]}",
                }

        return {
            "success": True,
            "output_path": output_path,
            "error": None,
            "frames_processed": enc_result.get("frames_processed", 0),
        }

    except Exception as exc:
        return {"success": False, "output_path": output_path, "error": str(exc)}

    finally:
        if dynamic_png:
            try:
                if os.path.exists(dynamic_png):
                    os.remove(dynamic_png)
            except OSError as e:
                print(f"Cleanup warning (PNG): {e}", flush=True)
        if tmp_dir:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=False)
            except OSError as e:
                print(f"Cleanup warning (tmp_dir): {e}", flush=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def _position_exprs(position: str) -> tuple[str, str]:
    """Return (x_expr, y_expr) FFmpeg overlay position strings with 20 px padding."""
    pad = 20
    positions = {
        "bottomright": (f"main_w-overlay_w-{pad}", f"main_h-overlay_h-{pad}"),
        "bottomleft":  (str(pad),                  f"main_h-overlay_h-{pad}"),
        "topright":    (f"main_w-overlay_w-{pad}", str(pad)),
        "topleft":     (str(pad),                  str(pad)),
        "center":      ("(main_w-overlay_w)/2",    "(main_h-overlay_h)/2"),
    }
    return positions.get(position, positions["bottomright"])
