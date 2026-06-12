"""
Camera controller for Arducam IMX519.

Primary: picamera2 (persistent session, OS3-derived)
  - Preview config (960x720) runs continuously with AfMode.Continuous
  - Photo config (4656x3496) triggered via autofocus_cycle() + switch_mode
  - Same pattern as openscan_firmware.controllers.hardware.cameras.picamera2

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
    """

    def __init__(self, settings: Optional[CameraSettings] = None):
        self.settings = settings or CameraSettings()
        self._busy = False
        self._lock = threading.Lock()

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

        common = {
            "AeEnable": False,
            "NoiseReductionMode": 0,
            "AwbEnable": False,
            "ExposureTime": s.shutter_us,
            "AnalogueGain": s.gain,
            "Saturation": s.saturation,
            "Contrast": s.contrast,
        }

        self._preview_cfg = self._picam.create_preview_configuration(
            main={"size": (960, 720)},
            controls=common,
        )
        self._photo_cfg = self._picam.create_still_configuration(
            main={"size": (s.width, s.height)},
            controls=common,
        )

        self._picam.configure(self._preview_cfg)
        self._picam.start()
        self._apply_focus(mode="preview")
        logger.info("picamera2 started — IMX519, persistent session")

    def _apply_focus(self, mode: str = "preview"):
        """Set AF mode: continuous for preview, one-shot for photo."""
        s = self.settings
        if s.autofocus:
            # Central 10% AF window (OS3 default)
            full_x, full_y = self._picam.camera_properties["PixelArraySize"]
            win_w = max(1, full_x // 10)
            win_h = max(1, full_y // 10)
            x0 = (full_x - win_w) // 2
            y0 = (full_y - win_h) // 2
            self._picam.set_controls({
                "AfMetering": _lc_controls.AfMeteringEnum.Windows,
                "AfWindows": [(x0, y0, win_w, win_h)],
            })
            af_mode = (_lc_controls.AfModeEnum.Continuous
                       if mode == "preview"
                       else _lc_controls.AfModeEnum.Auto)
            self._picam.set_controls({"AfMode": af_mode})
        else:
            lp = max(0.0, s.lens_position)
            self._picam.set_controls({
                "AfMode": _lc_controls.AfModeEnum.Manual,
                "LensPosition": lp,
            })

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

    def capture_jpeg(self) -> bytes:
        """Full-resolution capture (4656×3496). Blocks until AF converges."""
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
            self._busy = False

    def set_focus(self, lens_position: float) -> None:
        self.settings.autofocus = False
        self.settings.lens_position = max(0.0, lens_position)
        if _PICAM2_AVAILABLE:
            self._apply_focus(mode="preview")
        logger.info(f"Manual focus: {self.settings.lens_position:.2f} diopters")

    def set_autofocus(self, enabled: bool) -> None:
        self.settings.autofocus = enabled
        if _PICAM2_AVAILABLE:
            self._apply_focus(mode="preview")
        logger.info(f"Autofocus: {'on' if enabled else 'off'}")

    def set_exposure(self, shutter_us: int) -> None:
        self.settings.shutter_us = max(100, shutter_us)
        if _PICAM2_AVAILABLE:
            self._picam.set_controls({"ExposureTime": self.settings.shutter_us})
        logger.info(f"Exposure: {self.settings.shutter_us}µs")

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
        self._apply_focus(mode="photo")
        if self.settings.autofocus:
            self._picam.autofocus_cycle()

        req = self._picam.switch_mode_and_capture_request(self._photo_cfg, wait=True)
        try:
            array = req.make_array("main")
        finally:
            req.release()

        self._picam.switch_mode(self._preview_cfg)
        self._apply_focus(mode="preview")

        data = self._encode_jpeg(array)
        logger.info(f"picamera2 capture: {len(data):,} bytes @ {self.settings.width}×{self.settings.height}")
        return data

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
        data = buf.getvalue()
        logger.debug(f"Preview: {len(data):,} bytes @ 960×720")
        return data

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
                w_af = max(1, s.width // 5)
                h_af = max(1, s.height // 5)
                x_af = (s.width - w_af) // 2
                y_af = (s.height - h_af) // 2
                cmd += [
                    "--autofocus-mode", "auto",
                    "--autofocus-on-capture",
                    "--autofocus-speed", "fast",
                    "--autofocus-range", "normal",
                    "--autofocus-window", f"{x_af},{y_af},{w_af},{h_af}",
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
            logger.debug(f"rpicam preview: {len(data):,} bytes")
            return data
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
