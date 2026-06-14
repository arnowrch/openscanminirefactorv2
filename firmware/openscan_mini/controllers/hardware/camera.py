"""
Camera controller for Arducam IMX519.

Primary: picamera2 (persistent session, OS3-derived)
  - Preview config at 1920×1440 for good AF metering data; MJPEG stream
    encodes from this buffer so browser gets crisp frames.
  - Still config at 4656×3496 (full sensor).
  - AfWindows coordinates are always in full sensor pixel space (4656×3496).
  - autofocus_cycle() manages AfMode internally for capture — never pre-set.
  - _busy=True for the entire capture/AF duration; stream skips frames.

Fallback: rpicam-still subprocess (if picamera2 not installed).
"""

import io
import logging
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── picamera2 availability ──────────────────────────────────────────────────
_PICAM2_AVAILABLE = False
try:
    from picamera2 import Picamera2
    from libcamera import controls as _lc_controls
    _PICAM2_AVAILABLE = True
    logger.info("picamera2 available — using persistent camera session")
except Exception as _e:
    logger.warning(f"picamera2 not available ({_e}) — falling back to rpicam-still")

# ── rpicam-still fallback ───────────────────────────────────────────────────
_RPICAM_BIN: Optional[str] = None

def _find_rpicam() -> Optional[str]:
    for binary in ("rpicam-still", "libcamera-still"):
        r = subprocess.run(["which", binary], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    return None


class CameraSettings:
    def __init__(
        self,
        shutter_us: int = 50000,
        gain: float = 1.0,
        jpeg_quality: int = 95,
        saturation: float = 1.0,
        contrast: float = 1.0,
        autofocus: bool = True,
        lens_position: float = 0.0,
        width: int = 4656,
        height: int = 3496,
    ):
        self.shutter_us = shutter_us
        self.gain = gain
        self.jpeg_quality = jpeg_quality
        self.saturation = saturation
        self.contrast = contrast
        self.autofocus = autofocus
        self.lens_position = lens_position
        self.width = width
        self.height = height


# Stream output resolution — high enough to look good in browser, low enough
# to encode fast on Pi 4. Preview config is 1920×1440 so AF has good data.
_STREAM_WIDTH = 1280
_STREAM_HEIGHT = 960


class CameraController:
    """
    IMX519 camera controller with picamera2 persistent session.

    Thread safety model:
      - _busy is True for the ENTIRE duration of any capture or AF cycle.
      - grab_stream_frame() returns (b"", None) immediately when _busy=True.
      - This prevents concurrent picamera2 access which corrupts AF state.
    """

    def __init__(self, settings: Optional[CameraSettings] = None):
        self.settings = settings or CameraSettings()
        self._busy = False
        self._lock = threading.Lock()
        self._store = None

        if _PICAM2_AVAILABLE:
            self._init_picamera2()
        else:
            global _RPICAM_BIN
            _RPICAM_BIN = _find_rpicam()
            if _RPICAM_BIN:
                logger.info(f"Camera fallback: '{_RPICAM_BIN}'")
            else:
                logger.warning("rpicam-still not found — capture will fail")

    # ── picamera2 init ──────────────────────────────────────────────────────

    def _init_picamera2(self):
        s = self.settings
        self._picam = Picamera2()
        self._full_sensor_size = None  # cached after first use

        common = {
            "AeEnable": False,
            "NoiseReductionMode": 0,
            "AwbEnable": False,
            "ExposureTime": s.shutter_us,
            "AnalogueGain": s.gain,
            "Saturation": s.saturation,
            "Contrast": s.contrast,
        }

        # 1920×1440 preview — high enough for AF to meter well.
        # RGB888 format ensures capture_array() returns (H,W,3) in RGB order
        # so PIL/numpy get correct colors without channel-swap hacks.
        self._preview_cfg = self._picam.create_preview_configuration(
            main={"size": (1920, 1440), "format": "RGB888"},
            controls=common,
        )
        # Full-res still
        self._photo_cfg = self._picam.create_still_configuration(
            buffer_count=1,
            main={"size": (s.width, s.height), "format": "RGB888"},
            controls=common,
        )

        self._picam.configure(self._preview_cfg)
        self._picam.start()
        self._apply_continuous_af()
        logger.info("picamera2 started — IMX519, 1920×1440 preview, 4656×3496 still")

    def _get_full_sensor_size(self):
        """Return full sensor pixel dimensions (4656×3496 for IMX519). Cached."""
        if self._full_sensor_size is None:
            try:
                pix = self._picam.camera_properties["PixelArraySize"]
                full_x = pix.width if hasattr(pix, "width") else int(pix[0])
                full_y = pix.height if hasattr(pix, "height") else int(pix[1])
                self._full_sensor_size = (full_x, full_y)
            except Exception:
                self._full_sensor_size = (4656, 3496)  # IMX519 fallback
        return self._full_sensor_size

    def _get_af_window(self):
        """Central 10% AF window in full sensor pixel coords (OS3 default)."""
        full_x, full_y = self._get_full_sensor_size()
        win_w = max(1, full_x // 10)
        win_h = max(1, full_y // 10)
        x0 = (full_x - win_w) // 2
        y0 = (full_y - win_h) // 2
        return [(x0, y0, win_w, win_h)]

    def _set_focus_mode(self, mode: str = "preview") -> None:
        """
        Configure AF mode. Mirrors OS3's _configure_focus().

        mode="preview"  → AfMode.Continuous (lens hunts continuously)
        mode="photo"    → AfMode.Auto + AfWindows (ready for autofocus_cycle)
        mode="manual"   → AfMode.Manual + LensPosition from settings
        """
        if not self.settings.autofocus or mode == "manual":
            try:
                lp = max(0.0, self.settings.lens_position)
                self._picam.set_controls({
                    "AfMode": _lc_controls.AfModeEnum.Manual,
                    "LensPosition": lp,
                })
                logger.info(f"Manual focus: {lp:.2f}d")
            except Exception as e:
                logger.warning(f"Manual focus failed: {e}")
            return

        try:
            win = self._get_af_window()
            if mode == "photo":
                # OS3 exact: set AfMode.Auto BEFORE calling autofocus_cycle()
                # If Continuous is set, autofocus_cycle() trigger is ignored → AfState stays null
                # AfRange.Macro: OpenScan Mini camera is 10-30cm from object — macro range only.
                # Full-range scan wastes time hunting infinity which is never the target.
                self._picam.set_controls({
                    "AfMetering": _lc_controls.AfMeteringEnum.Windows,
                    "AfWindows": win,
                    "AfMode": _lc_controls.AfModeEnum.Auto,
                    "AfRange": _lc_controls.AfRangeEnum.Macro,
                    "AfSpeed": _lc_controls.AfSpeedEnum.Fast,
                })
                logger.info(f"AF mode=Auto range=Macro (photo), window={win}")
            else:
                # preview: Continuous so lens stays responsive
                self._picam.set_controls({
                    "AfMetering": _lc_controls.AfMeteringEnum.Windows,
                    "AfWindows": win,
                    "AfMode": _lc_controls.AfModeEnum.Continuous,
                    "AfSpeed": _lc_controls.AfSpeedEnum.Fast,
                })
                logger.info(f"AF mode=Continuous (preview)")
        except Exception as e:
            logger.warning(f"AF mode={mode} setup failed: {e}")
            try:
                target = _lc_controls.AfModeEnum.Auto if mode == "photo" else _lc_controls.AfModeEnum.Continuous
                self._picam.set_controls({"AfMode": target})
            except Exception as e2:
                logger.error(f"AF mode fallback failed: {e2}")

    # Keep old name as alias so existing callers still work
    def _apply_continuous_af(self):
        self._set_focus_mode("preview")

    def _apply_focus(self, mode: str = "preview"):
        self._set_focus_mode(mode)

    def _encode_jpeg(self, array, quality: int = None) -> bytes:
        try:
            from PIL import Image
        except ImportError:
            raise RuntimeError("Pillow not installed: pip install Pillow")
        q = quality if quality is not None else self.settings.jpeg_quality
        # picamera2 "RGB888" stores B,G,R in memory — swap to RGB for PIL
        if array.ndim == 3 and array.shape[2] == 3:
            array = array[:, :, ::-1]
        elif array.ndim == 3 and array.shape[2] == 4:
            array = array[:, :, 2::-1]
        img = Image.fromarray(array)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=q)
        return buf.getvalue()

    # ── Public API ──────────────────────────────────────────────────────────

    def grab_stream_frame(self) -> tuple:
        """
        Grab one frame for the MJPEG stream.
        Returns (jpeg_bytes, rgb_array | None).

        Skips frame (returns b"", None) when camera is busy with a still capture
        or AF cycle — prevents concurrent picamera2 access.

        Output is resized to _STREAM_WIDTH × _STREAM_HEIGHT for smooth browser
        playback even though the internal buffer is 1920×1440.
        """
        if not _PICAM2_AVAILABLE:
            try:
                return self._capture_preview_rpicam(), None
            except Exception:
                return b"", None

        if self._busy:
            return b"", None

        try:
            array = self._picam.capture_array("main")
            from PIL import Image
            import numpy as np
            # picamera2 "RGB888" = V4L2_PIX_FMT_BGR24 (B,G,R in memory).
            # PIL treats axis-2 index 0 as Red → swap to get correct colors.
            if array.ndim == 3 and array.shape[2] == 3:
                array = array[:, :, ::-1]  # BGR → RGB
            elif array.ndim == 3 and array.shape[2] == 4:
                array = array[:, :, 2::-1]  # XRGB/BGRX → RGB
            img = Image.fromarray(array)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            # Resize to stream size for browser
            img = img.resize((_STREAM_WIDTH, _STREAM_HEIGHT), Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=72)
            rgb = np.array(img)
            return buf.getvalue(), rgb
        except Exception as e:
            logger.debug(f"Stream frame error: {e}")
            return b"", None

    def apply_auto_adjust(self, suggested_shutter_us=None, suggested_gain=None) -> None:
        """Apply auto-exposure/gain suggestion from analysis pipeline."""
        if suggested_shutter_us and _PICAM2_AVAILABLE:
            self.settings.shutter_us = suggested_shutter_us
            self._picam.set_controls({"ExposureTime": suggested_shutter_us})
        if suggested_gain and _PICAM2_AVAILABLE:
            self.settings.gain = suggested_gain
            self._picam.set_controls({"AnalogueGain": suggested_gain})

    def capture_jpeg(self) -> bytes:
        """Full-resolution capture (4656×3496). _busy=True for entire duration."""
        with self._lock:
            if self._busy:
                raise RuntimeError("Camera busy")
            self._busy = True
        try:
            if _PICAM2_AVAILABLE:
                return self._capture_jpeg_picam2()
            else:
                return self._capture_jpeg_rpicam()
        finally:
            with self._lock:
                self._busy = False

    def capture_preview(self) -> bytes:
        """Fast preview frame for snapshot endpoint."""
        with self._lock:
            if self._busy:
                raise RuntimeError("Camera busy")
            self._busy = True
        try:
            if _PICAM2_AVAILABLE:
                return self._capture_preview_picam2()
            else:
                return self._capture_preview_rpicam()
        finally:
            with self._lock:
                self._busy = False

    def set_settings_store(self, store) -> None:
        self._store = store

    def _persist_settings(self) -> None:
        if self._store is None:
            return
        try:
            s = self.settings
            data = {
                "shutter_us": s.shutter_us,
                "gain": s.gain,
                "jpeg_quality": s.jpeg_quality,
                "saturation": s.saturation,
                "contrast": s.contrast,
                "autofocus": s.autofocus,
                "lens_position": s.lens_position,
                "width": s.width,
                "height": s.height,
            }
            current = self._store.load()
            updated = current.model_copy(update=data)
            self._store.save(updated)
        except Exception as e:
            logger.warning(f"Failed to persist camera settings: {e}")

    def set_focus(self, lens_position: float) -> None:
        self.settings.autofocus = False
        self.settings.lens_position = max(0.0, lens_position)
        if _PICAM2_AVAILABLE:
            self._apply_continuous_af()
        self._persist_settings()
        logger.info(f"Manual focus: {self.settings.lens_position:.2f} diopters")

    def set_autofocus(self, enabled: bool) -> None:
        self.settings.autofocus = enabled
        if _PICAM2_AVAILABLE:
            self._apply_continuous_af()
        self._persist_settings()
        logger.info(f"Autofocus: {'on' if enabled else 'off'}")

    def set_exposure(self, shutter_us: int, gain: Optional[float] = None) -> None:
        self.settings.shutter_us = max(100, shutter_us)
        if gain is not None:
            self.settings.gain = max(0.1, gain)
        if _PICAM2_AVAILABLE:
            ctrl = {"ExposureTime": self.settings.shutter_us}
            if gain is not None:
                ctrl["AnalogueGain"] = self.settings.gain
            self._picam.set_controls(ctrl)
        self._persist_settings()
        logger.info(f"Exposure: {self.settings.shutter_us}µs gain={self.settings.gain}")

    def get_status(self) -> dict:
        mode = "picamera2" if _PICAM2_AVAILABLE else f"rpicam-still ({_RPICAM_BIN})"
        return {
            "camera": "IMX519",
            "interface": "CSI (ribbon cable)",
            "mode": mode,
            "available": _PICAM2_AVAILABLE or bool(_RPICAM_BIN),
            "busy": self._busy,
            "settings": {
                "width": self.settings.width,
                "height": self.settings.height,
                "shutter_us": self.settings.shutter_us,
                "gain": self.settings.gain,
                "jpeg_quality": self.settings.jpeg_quality,
                "saturation": self.settings.saturation,
                "contrast": self.settings.contrast,
                "autofocus": self.settings.autofocus,
                "lens_position": self.settings.lens_position,
            },
        }

    def cleanup(self) -> None:
        if _PICAM2_AVAILABLE and hasattr(self, "_picam"):
            try:
                self._picam.stop()
                self._picam.close()
            except Exception:
                pass

    # ── picamera2 capture internals ─────────────────────────────────────────

    def _trigger_af_and_wait(self, wait_s: float = 3.0) -> None:
        """
        Time-based AF trigger — equivalent to 'libcamera-still --autofocus -t 3000'.

        autofocus_cycle() is NOT usable on this Pi: it internally accesses
        metadata['AfState'] with a direct key lookup, but IMX519 on this
        libcamera build never populates AfState → KeyError crash every time.

        Instead: set AfMode.Auto + AfRange.Macro + AfTrigger.Start, then wait.
        The VCM physically moves and settles within ~2-3s. No AfState needed.
        """
        import time
        try:
            self._picam.set_controls({
                "AfTrigger": _lc_controls.AfTriggerEnum.Start,
            })
            logger.info(f"AF trigger fired (time-based, {wait_s}s wait)")
            time.sleep(wait_s)
            logger.info("AF wait done — VCM should have settled")
        except Exception as e:
            logger.warning(f"AF trigger error: {e}")

    def _capture_jpeg_picam2(self) -> bytes:
        """
        Full-res still with time-based AF (libcamera-still --autofocus equivalent).
          1. _set_focus_mode("photo") → AfMode.Auto + AfRange.Macro + AfWindows
          2. _trigger_af_and_wait(3s) → AfTrigger.Start + sleep (VCM settles)
          3. switch_mode_and_capture_request → full-res still
          4. switch_mode back to preview + restore AfMode.Continuous
        """
        if self.settings.autofocus:
            self._set_focus_mode("photo")
            self._trigger_af_and_wait(3.0)

        # 3 attempts with exponential backoff (OS3 pattern)
        last_exc = None
        for attempt in range(1, 4):
            try:
                req = self._picam.switch_mode_and_capture_request(self._photo_cfg, wait=True)
                try:
                    array = req.make_array("main")
                finally:
                    req.release()

                self._picam.switch_mode(self._preview_cfg)
                # Step 5: restore Continuous for preview (OS3 does this after every capture)
                self._set_focus_mode("preview")

                data = self._encode_jpeg(array)
                logger.info(f"Captured {len(data):,} bytes @ {self.settings.width}×{self.settings.height}")
                return data
            except Exception as e:
                last_exc = e
                logger.warning(f"Capture attempt {attempt}/3 failed: {e}")
                if attempt < 3:
                    import time
                    time.sleep([1, 2][attempt - 1])

        raise RuntimeError(f"Capture failed after 3 attempts: {last_exc}")

    def _capture_preview_picam2(self) -> bytes:
        array = self._picam.capture_array("main")
        return self._encode_jpeg(array, quality=75)

    # ── rpicam-still fallback ────────────────────────────────────────────────

    def _capture_jpeg_rpicam(self) -> bytes:
        if not _RPICAM_BIN:
            raise RuntimeError("rpicam-still not found.")
        s = self.settings
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cmd = [
                _RPICAM_BIN,
                "--output", tmp_path, "--timeout", "4000", "--nopreview",
                "--width", str(s.width), "--height", str(s.height),
                "--shutter", str(s.shutter_us), "--gain", str(s.gain),
                "--quality", str(s.jpeg_quality),
                "--saturation", str(s.saturation), "--contrast", str(s.contrast),
                "--encoding", "jpg",
            ]
            if s.autofocus:
                cmd += ["--autofocus-mode", "auto", "--autofocus-on-capture",
                        "--autofocus-speed", "fast", "--autofocus-range", "normal"]
            else:
                cmd += ["--autofocus-mode", "manual",
                        "--lens-position", f"{s.lens_position:.4f}", "--immediate"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                raise RuntimeError(f"rpicam-still failed: {result.stderr.strip()}")
            data = Path(tmp_path).read_bytes()
            if not data:
                raise RuntimeError("rpicam-still produced empty file")
            return data
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

    def _capture_preview_rpicam(self) -> bytes:
        if not _RPICAM_BIN:
            raise RuntimeError("rpicam-still not found.")
        s = self.settings
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cmd = [
                _RPICAM_BIN, "--output", tmp_path, "--timeout", "500",
                "--nopreview", "--immediate",
                "--width", str(_STREAM_WIDTH), "--height", str(_STREAM_HEIGHT),
                "--autofocus-mode", "manual",
                "--lens-position", f"{s.lens_position:.4f}",
                "--quality", "72", "--encoding", "jpg",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise RuntimeError(f"Preview failed: {result.stderr.strip()}")
            return Path(tmp_path).read_bytes()
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
