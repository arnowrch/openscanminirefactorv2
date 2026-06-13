"""
Camera controller for Arducam IMX519.

Primary: picamera2 (persistent session, OS3-derived)
  - Preview config (960x720) runs continuously with AfMode.Continuous
  - Photo config (4656x3496) triggered via autofocus_cycle() + switch_mode
  - autofocus_cycle() manages AfMode internally — never pre-set AfMode before it
  - CONCURRENCY: _busy flag prevents stream from reading during capture/AF

Fallback: rpicam-still subprocess (if picamera2 not installed)
  Install picamera2: sudo apt install python3-picamera2
  Then recreate venv: python3 -m venv --system-site-packages ~/openscan_env
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
    logger.info("picamera2 available — using persistent camera session (OS3 mode)")
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


class CameraController:
    """
    IMX519 camera controller.

    Uses picamera2 when available (persistent session, fast AF via autofocus_cycle).
    Falls back to rpicam-still subprocess when picamera2 is not installed.

    Thread safety:
      _busy=True during any capture or AF cycle.
      grab_stream_frame() skips frames while _busy — never touches picamera2 concurrently.
    """

    def __init__(self, settings: Optional[CameraSettings] = None):
        self.settings = settings or CameraSettings()
        self._busy = False
        self._lock = threading.Lock()
        self._store = None  # SettingsStore — injected from main.py via set_settings_store()

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

        # Exposure/gain controls shared by both configs
        common = {
            "AeEnable": False,
            "NoiseReductionMode": 0,
            "AwbEnable": False,
            "ExposureTime": s.shutter_us,
            "AnalogueGain": s.gain,
            "Saturation": s.saturation,
            "Contrast": s.contrast,
        }

        # Preview at 960×720 for MJPEG stream (~12fps on Pi 4)
        self._preview_cfg = self._picam.create_preview_configuration(
            main={"size": (960, 720)},
            controls=common,
        )
        # Still at full sensor resolution
        self._photo_cfg = self._picam.create_still_configuration(
            buffer_count=1,
            main={"size": (s.width, s.height), "format": "RGB888"},
            controls=common,
        )

        self._picam.configure(self._preview_cfg)
        self._picam.start()
        self._apply_continuous_af()
        logger.info("picamera2 started — IMX519, persistent session")

    def _apply_continuous_af(self):
        """Enable continuous AF for live preview. Call after start() and after each still capture."""
        if not self.settings.autofocus:
            try:
                lp = max(0.0, self.settings.lens_position)
                self._picam.set_controls({
                    "AfMode": _lc_controls.AfModeEnum.Manual,
                    "LensPosition": lp,
                })
                logger.info(f"Manual focus: {lp:.2f} diopters")
            except Exception as e:
                logger.warning(f"Manual focus set failed: {e}")
            return
        try:
            self._picam.set_controls({
                "AfMode": _lc_controls.AfModeEnum.Continuous,
                "AfSpeed": _lc_controls.AfSpeedEnum.Fast,
            })
            logger.info("AF continuous enabled")
        except Exception as e:
            logger.warning(f"Continuous AF set failed: {e}")

    # Keep old name as alias — used by setters
    def _apply_focus(self, mode: str = "preview"):
        self._apply_continuous_af()

    def _encode_jpeg(self, array) -> bytes:
        try:
            from PIL import Image
        except ImportError:
            raise RuntimeError("Pillow not installed: pip install Pillow")
        img = Image.fromarray(array)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.settings.jpeg_quality)
        return buf.getvalue()

    # ── Public API ──────────────────────────────────────────────────────────

    def grab_stream_frame(self) -> tuple:
        """
        Grab one frame for the MJPEG stream.
        Returns (jpeg_bytes, rgb_array | None).
        Returns (b"", None) silently when camera is busy with a capture.
        Never raises — so the stream loop keeps running regardless.
        """
        if not _PICAM2_AVAILABLE:
            try:
                return self._capture_preview_rpicam(), None
            except Exception:
                return b"", None

        # Skip frame if a still capture or AF cycle is in progress
        if self._busy:
            return b"", None

        try:
            array = self._picam.capture_array("main")
            from PIL import Image
            img = Image.fromarray(array)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=72)
            import numpy as np
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
        """Full-resolution capture (4656×3496). Sets _busy=True for full duration."""
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
        """Fast 960×720 preview frame (no AF, ~200ms)."""
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
        """Inject a SettingsStore so setters auto-persist camera settings."""
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

    def _capture_jpeg_picam2(self) -> bytes:
        """
        Full-res still capture with autofocus.

        AF flow (matches OS3 pattern):
          1. autofocus_cycle() sets AfMode.Auto + AfTrigger.Start internally
          2. Waits for AfState = Focused or Failed
          3. switch_mode captures in still config
          4. Restore continuous AF for stream

        IMPORTANT: do NOT call set_controls(AfMode.Auto) before autofocus_cycle().
        autofocus_cycle() manages this internally. Pre-setting AfMode causes
        AfState to be absent from metadata on some libcamera builds → KeyError.
        """
        if self.settings.autofocus:
            try:
                success = self._picam.autofocus_cycle(timeout=5)
                if not success:
                    logger.warning("AF cycle timed out — capturing with current focus")
            except Exception as e:
                logger.warning(f"AF cycle error ({e}) — capturing with current focus")

        # Retry up to 3 times with exponential backoff (like OS3)
        last_exc = None
        for attempt in range(1, 4):
            try:
                req = self._picam.switch_mode_and_capture_request(self._photo_cfg, wait=True)
                try:
                    array = req.make_array("main")
                finally:
                    req.release()

                # Restore continuous AF before returning to stream
                self._picam.switch_mode(self._preview_cfg)
                self._apply_continuous_af()

                data = self._encode_jpeg(array)
                logger.info(f"picamera2 capture: {len(data):,} bytes @ {self.settings.width}×{self.settings.height}")
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
        buf = io.BytesIO()
        try:
            from PIL import Image
            img = Image.fromarray(array)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=75)
        except ImportError:
            raise RuntimeError("Pillow not installed: pip install Pillow")
        return buf.getvalue()

    # ── rpicam-still fallback internals ─────────────────────────────────────

    def _capture_jpeg_rpicam(self) -> bytes:
        if not _RPICAM_BIN:
            raise RuntimeError("rpicam-still not found. Install picamera2 or rpicam-apps.")
        s = self.settings
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cmd = [
                _RPICAM_BIN,
                "--output", tmp_path,
                "--timeout", "4000",
                "--nopreview",
                "--width", str(s.width), "--height", str(s.height),
                "--shutter", str(s.shutter_us),
                "--gain", str(s.gain),
                "--quality", str(s.jpeg_quality),
                "--saturation", str(s.saturation),
                "--contrast", str(s.contrast),
                "--encoding", "jpg",
            ]
            if s.autofocus:
                cmd += [
                    "--autofocus-mode", "auto",
                    "--autofocus-on-capture",
                    "--autofocus-speed", "fast",
                    "--autofocus-range", "normal",
                ]
            else:
                cmd += ["--autofocus-mode", "manual",
                        "--lens-position", f"{s.lens_position:.4f}",
                        "--immediate"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                raise RuntimeError(f"rpicam-still failed: {result.stderr.strip()}")
            data = Path(tmp_path).read_bytes()
            if not data:
                raise RuntimeError("rpicam-still produced empty file")
            logger.info(f"rpicam capture: {len(data):,} bytes")
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
                _RPICAM_BIN,
                "--output", tmp_path,
                "--timeout", "500",
                "--nopreview", "--immediate",
                "--width", "960", "--height", "720",
                "--autofocus-mode", "manual",
                "--lens-position", f"{s.lens_position:.4f}",
                "--quality", "75",
                "--encoding", "jpg",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise RuntimeError(f"Preview failed: {result.stderr.strip()}")
            data = Path(tmp_path).read_bytes()
            return data
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
