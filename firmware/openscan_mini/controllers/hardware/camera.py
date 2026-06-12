"""
Camera controller for Arducam IMX519 (CSI/ribbon cable) via rpicam-still.

Hardware verified from OS2 firmware image:
  Camera:   IMX519 @ /base/soc/i2c0mux/i2c@1/imx519@1a
  Overlay:  dtoverlay=imx519,media-controller=1
  OS2 defaults: cam_focus=2838, cam_shutter=50000us, jpeg_quality=95

rpicam-still is used instead of picamera2 (picamera2 is apt-only and
cannot be pip-installed into a venv). For autofocus the IMX519 supports
PDAF — we default to continuous AF but allow switching to manual mode.
"""

import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_RPICAM_BIN: Optional[str] = None


def _find_rpicam() -> Optional[str]:
    """Locate rpicam-still or libcamera-still binary."""
    for binary in ("rpicam-still", "libcamera-still"):
        result = subprocess.run(
            ["which", binary], capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
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
        lens_position: float = 0.0,  # 0.0=infinity, higher=closer (diopters)
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
    Controls Arducam IMX519 CSI camera via rpicam-still subprocess.

    Each capture opens a new rpicam-still process, captures to a temp file,
    reads the bytes, and cleans up. Simple and reliable on Pi OS Bookworm.
    """

    def __init__(
        self,
        settings: Optional[CameraSettings] = None,
    ):
        self.settings = settings or CameraSettings()
        self._busy = False

        global _RPICAM_BIN
        _RPICAM_BIN = _find_rpicam()

        if _RPICAM_BIN:
            logger.info(f"CameraController ready using '{_RPICAM_BIN}' — IMX519 CSI")
        else:
            logger.warning("rpicam-still / libcamera-still not found — capture will fail")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture_jpeg(self) -> bytes:
        """
        Capture a single JPEG from the IMX519.

        Returns raw JPEG bytes. Raises RuntimeError on failure.
        """
        if self._busy:
            raise RuntimeError("Camera is already capturing.")
        if not _RPICAM_BIN:
            raise RuntimeError("rpicam-still not found. Install rpicam-apps.")

        self._busy = True
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name

            cmd = self._build_cmd(tmp_path)
            logger.debug(f"Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode != 0:
                logger.error(f"rpicam-still failed: {result.stderr}")
                raise RuntimeError(f"Capture failed: {result.stderr.strip()}")

            path = Path(tmp_path)
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError("rpicam-still produced no output file.")

            data = path.read_bytes()
            logger.info(f"Captured JPEG: {len(data):,} bytes @ {self.settings.width}x{self.settings.height}")
            return data

        finally:
            self._busy = False
            with Path(tmp_path).open("w") as _:
                pass  # truncate
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

    def set_focus(self, lens_position: float) -> None:
        """
        Set manual focus lens position.

        0.0 = infinity, ~10.0 = ~10cm (1/distance in diopters).
        Disables autofocus.
        """
        self.settings.autofocus = False
        self.settings.lens_position = max(0.0, lens_position)
        logger.info(f"Focus set: lens_position={self.settings.lens_position:.2f}, AF disabled")

    def set_autofocus(self, enabled: bool = True) -> None:
        self.settings.autofocus = enabled
        logger.info(f"Autofocus: {'enabled' if enabled else 'disabled'}")

    def set_exposure(self, shutter_us: int) -> None:
        self.settings.shutter_us = max(100, shutter_us)
        logger.info(f"Exposure set: shutter={self.settings.shutter_us}µs")

    def get_status(self) -> dict:
        return {
            "camera": "IMX519",
            "interface": "CSI (ribbon cable)",
            "binary": _RPICAM_BIN,
            "available": _RPICAM_BIN is not None,
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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_cmd(self, output_path: str) -> list:
        s = self.settings
        cmd = [
            _RPICAM_BIN,
            "--output", output_path,
            "--timeout", "2000",       # 2s preview/settle time
            "--nopreview",
            "--immediate",
            "--width", str(s.width),
            "--height", str(s.height),
            "--shutter", str(s.shutter_us),
            "--gain", str(s.gain),
            "--quality", str(s.jpeg_quality),
            "--saturation", str(s.saturation),
            "--contrast", str(s.contrast),
            "--encoding", "jpg",
        ]

        if s.autofocus:
            cmd += ["--autofocus-mode", "auto"]
        else:
            cmd += [
                "--autofocus-mode", "manual",
                "--lens-position", f"{s.lens_position:.4f}",
            ]

        return cmd
