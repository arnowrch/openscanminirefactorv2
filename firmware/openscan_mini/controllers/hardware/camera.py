"""
Camera controller for UC-873 Rev.D USB camera via linuxpy (V4L2).

Capture settings from OS2 firmware:
  cam_focus=2838, cam_shutter=50000µs, jpeg_quality=95

Focus/exposure are applied via v4l2-ctl subprocess because linuxpy's
set_control() interface is device-dependent. MJPG is used for capture;
the first frame is always discarded as V4L2 devices often deliver stale
buffers on first acquire.
"""

import io
import logging
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_SIMULATION = False
try:
    from linuxpy.video.device import Device, VideoCapture  # type: ignore[import]
except ImportError:
    logger.warning("linuxpy not available — camera running in simulation mode")
    _SIMULATION = True


DEFAULT_DEVICE = "/dev/video0"
DEFAULT_RESOLUTION: Tuple[int, int] = (1920, 1080)
MJPG = "MJPG"


class CameraSettings:
    """Mutable settings applied to the hardware on change."""

    def __init__(
        self,
        focus: int = 2838,
        shutter_us: int = 50000,
        jpeg_quality: int = 95,
        saturation: Optional[int] = None,
        contrast: Optional[int] = None,
        gain: Optional[int] = None,
        resolution: Tuple[int, int] = DEFAULT_RESOLUTION,
    ):
        self.focus = focus
        self.shutter_us = shutter_us
        self.jpeg_quality = jpeg_quality
        self.saturation = saturation
        self.contrast = contrast
        self.gain = gain
        self.resolution = resolution


class CameraController:
    """
    Controls UC-873 Rev.D USB camera via linuxpy (V4L2).

    Exposes:
      capture_jpeg() → bytes
      set_focus(value)
      set_exposure(shutter_us)
      get_status() → dict
    """

    def __init__(
        self,
        device_path: str = DEFAULT_DEVICE,
        settings: Optional[CameraSettings] = None,
    ):
        self.device_path = device_path
        self.settings = settings or CameraSettings()
        self._busy = False

        if _SIMULATION:
            logger.warning(f"Camera '{device_path}' running in simulation mode.")
            return

        if not Path(device_path).exists():
            logger.warning(f"Camera device '{device_path}' not found. Capture will fail.")
            return

        # Apply initial v4l2-ctl settings
        self._apply_v4l2_settings(self.settings)
        logger.info(f"CameraController ready: {device_path} @ {self.settings.resolution}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture_jpeg(self) -> bytes:
        """
        Capture one JPEG frame from the camera.

        Opens the device, sets MJPG format, discards the first frame,
        returns the second as bytes.
        """
        if _SIMULATION:
            return self._simulated_jpeg()

        if self._busy:
            raise RuntimeError("Camera is already capturing.")

        self._busy = True
        try:
            width, height = self.settings.resolution
            with Device(self.device_path) as device:
                capture = VideoCapture(device)
                capture.set_format(width, height, MJPG)
                with capture:
                    frame_iter = iter(capture)
                    next(frame_iter, None)  # discard first frame
                    frame = next(frame_iter)
                    data = bytes(frame)
                    logger.debug(f"Captured JPEG: {len(data)} bytes")
                    return data
        except StopIteration as exc:
            raise RuntimeError("Camera did not deliver a frame.") from exc
        finally:
            self._busy = False

    def set_focus(self, value: int) -> None:
        """Set manual focus via v4l2-ctl. Range: 0–1023 for UC-873."""
        self.settings.focus = value
        self._v4l2_ctl("focus_absolute", value)

    def set_exposure(self, shutter_us: int) -> None:
        """Set exposure time in microseconds via v4l2-ctl."""
        self.settings.shutter_us = shutter_us
        # V4L2 exposure_absolute unit is 100µs for most UVC cameras
        exposure_100us = max(1, shutter_us // 100)
        self._v4l2_ctl("exposure_absolute", exposure_100us)

    def get_status(self) -> dict:
        return {
            "device": self.device_path,
            "available": Path(self.device_path).exists() or _SIMULATION,
            "simulation": _SIMULATION,
            "busy": self._busy,
            "settings": {
                "focus": self.settings.focus,
                "shutter_us": self.settings.shutter_us,
                "jpeg_quality": self.settings.jpeg_quality,
                "resolution": list(self.settings.resolution),
                "saturation": self.settings.saturation,
                "contrast": self.settings.contrast,
                "gain": self.settings.gain,
            },
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_v4l2_settings(self, s: CameraSettings) -> None:
        """Push all relevant settings to the device on init."""
        # Disable autofocus so manual focus takes effect
        self._v4l2_ctl("focus_auto", 0)
        self._v4l2_ctl("focus_absolute", s.focus)
        # Disable auto-exposure, set manual
        self._v4l2_ctl("exposure_auto", 1)  # 1 = manual for most UVC
        exposure_100us = max(1, s.shutter_us // 100)
        self._v4l2_ctl("exposure_absolute", exposure_100us)
        if s.saturation is not None:
            self._v4l2_ctl("saturation", s.saturation)
        if s.contrast is not None:
            self._v4l2_ctl("contrast", s.contrast)
        if s.gain is not None:
            self._v4l2_ctl("gain", s.gain)

    def _v4l2_ctl(self, control: str, value: int) -> None:
        """Run v4l2-ctl to set a single control. Logs warnings on failure."""
        cmd = ["v4l2-ctl", f"--device={self.device_path}", f"--set-ctrl={control}={value}"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if result.returncode != 0:
                logger.warning(f"v4l2-ctl {control}={value} failed: {result.stderr.strip()}")
            else:
                logger.debug(f"v4l2-ctl {control}={value} OK")
        except FileNotFoundError:
            logger.warning("v4l2-ctl not found — install v4l-utils for hardware control")
        except subprocess.TimeoutExpired:
            logger.warning(f"v4l2-ctl {control}={value} timed out")

    def _simulated_jpeg(self) -> bytes:
        """Return a minimal valid JPEG for simulation testing."""
        import struct
        # 1x1 pixel white JPEG
        return bytes([
            0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
            0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
            0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
            0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
            0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
            0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
            0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
            0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
            0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00,
            0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
            0x09, 0x0A, 0x0B, 0xFF, 0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
            0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D,
            0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
            0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
            0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
            0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
            0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
            0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
            0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
            0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
            0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
            0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
            0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
            0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
            0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
            0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,
            0x00, 0x00, 0x3F, 0x00, 0xFB, 0xD2, 0x8A, 0x28, 0x03, 0xFF, 0xD9,
        ])
