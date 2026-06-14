"""
Image analysis pipeline for live preview quality feedback.

Runs on downscaled frames (480x360) so all operations stay <10ms on Pi 4.
cv2 used when available; numpy-only fallback for all metrics.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_CV2_AVAILABLE = False
try:
    import cv2 as _cv2
    _CV2_AVAILABLE = True
except ImportError:
    pass

_ANALYSIS_SIZE = (480, 360)  # downscale target for analysis pass


@dataclass
class FrameAnalysis:
    sharpness: float = 0.0       # Laplacian variance — higher = sharper
    brightness: float = 0.0      # mean luma 0–255
    is_sharp: bool = False        # sharpness >= threshold
    is_exposed: bool = False      # brightness in acceptable range
    crop_box: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h) in preview coords
    suggested_shutter_us: Optional[int] = None
    suggested_gain: Optional[float] = None


# Tuneable thresholds
SHARPNESS_MIN = 80.0      # below this → image is blurry
BRIGHTNESS_LOW = 40.0     # below → underexposed
BRIGHTNESS_HIGH = 210.0   # above → overexposed
BRIGHTNESS_TARGET = 128.0
SHUTTER_MIN_US = 500
SHUTTER_MAX_US = 500_000
GAIN_MIN = 1.0
GAIN_MAX = 8.0


def analyze(rgb_array: np.ndarray,
            current_shutter_us: int = 50_000,
            current_gain: float = 1.0) -> FrameAnalysis:
    """
    Full analysis pass on an RGB frame.
    Returns FrameAnalysis with metrics and suggested corrections.
    """
    result = FrameAnalysis()

    # Downscale for fast processing
    small = _resize(rgb_array, _ANALYSIS_SIZE)
    gray = _to_gray(small)

    # Sharpness on full frame
    result.sharpness = _sharpness(gray)
    result.is_sharp = result.sharpness >= SHARPNESS_MIN

    # Brightness on centre 20% crop — avoids dark background pulling mean down
    # (same region as the AF crosshair, so "Exp OK" reflects the object, not background)
    h, w = gray.shape[:2]
    cy, cx = h // 2, w // 2
    dh, dw = h // 10, w // 10
    center_crop = gray[cy - dh: cy + dh, cx - dw: cx + dw]
    result.brightness = float(center_crop.mean())
    result.is_exposed = BRIGHTNESS_LOW <= result.brightness <= BRIGHTNESS_HIGH

    # Auto-exposure suggestion (proportional correction)
    if result.brightness > 0:
        ratio = BRIGHTNESS_TARGET / result.brightness
        new_shutter = int(current_shutter_us * ratio)
        new_shutter = max(SHUTTER_MIN_US, min(SHUTTER_MAX_US, new_shutter))
        if abs(new_shutter - current_shutter_us) > current_shutter_us * 0.15:
            result.suggested_shutter_us = new_shutter

        # If shutter is maxed and still dark, nudge gain up
        if new_shutter >= SHUTTER_MAX_US and result.brightness < BRIGHTNESS_LOW:
            new_gain = min(GAIN_MAX, current_gain * ratio)
            if new_gain > current_gain * 1.1:
                result.suggested_gain = round(new_gain, 2)
        elif result.brightness > BRIGHTNESS_HIGH and current_gain > GAIN_MIN * 1.1:
            # Overexposed: reduce gain proportionally (multiply by ratio < 1)
            result.suggested_gain = round(max(GAIN_MIN, current_gain * ratio), 2)

    # Crop box: find the dominant object region
    result.crop_box = _detect_object_box(gray, rgb_array.shape)

    return result


# ── Internal helpers ─────────────────────────────────────────────────────────

def _resize(arr: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
    w, h = size
    if _CV2_AVAILABLE:
        return _cv2.resize(arr, (w, h), interpolation=_cv2.INTER_AREA)
    # Simple numpy slice downsample
    sh, sw = arr.shape[:2]
    sy = max(1, sh // h)
    sx = max(1, sw // w)
    return arr[::sy, ::sx][:h, :w]


def _to_gray(rgb: np.ndarray) -> np.ndarray:
    if _CV2_AVAILABLE:
        return _cv2.cvtColor(rgb, _cv2.COLOR_RGB2GRAY)
    # Rec.709 luma
    return (rgb[..., 0] * 0.2126 +
            rgb[..., 1] * 0.7152 +
            rgb[..., 2] * 0.0722).astype(np.uint8)


def _sharpness(gray: np.ndarray) -> float:
    if _CV2_AVAILABLE:
        lap = _cv2.Laplacian(gray, _cv2.CV_64F)
        return float(lap.var())
    # Numpy fallback: Sobel-like gradient variance
    gy = np.diff(gray.astype(np.float32), axis=0)
    gx = np.diff(gray.astype(np.float32), axis=1)
    h = min(gy.shape[0], gx.shape[0])
    w = min(gy.shape[1], gx.shape[1])
    mag = np.sqrt(gy[:h, :w] ** 2 + gx[:h, :w] ** 2)
    return float(mag.var())


def _detect_object_box(gray: np.ndarray,
                       original_shape: Tuple) -> Optional[Tuple[int, int, int, int]]:
    """
    Detect the largest non-background region.
    Returns bbox scaled back to original frame coordinates.
    Returns None if no clear object found.
    """
    try:
        h, w = gray.shape
        oh, ow = original_shape[:2]

        # Ignore outer 10% (often turntable edge/background)
        margin_y, margin_x = h // 10, w // 10
        roi = gray[margin_y:h - margin_y, margin_x:w - margin_x]

        # Threshold: pixels brighter than background median
        med = float(np.median(roi))
        thresh = (roi > med * 0.85).astype(np.uint8) * 255

        if _CV2_AVAILABLE:
            contours, _ = _cv2.findContours(thresh, _cv2.RETR_EXTERNAL,
                                            _cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None
            largest = max(contours, key=_cv2.contourArea)
            if _cv2.contourArea(largest) < (roi.size * 0.02):
                return None
            x, y, bw, bh = _cv2.boundingRect(largest)
        else:
            rows = np.any(thresh, axis=1)
            cols = np.any(thresh, axis=0)
            if not rows.any():
                return None
            y0, y1 = np.where(rows)[0][[0, -1]]
            x0, x1 = np.where(cols)[0][[0, -1]]
            x, y, bw, bh = int(x0), int(y0), int(x1 - x0), int(y1 - y0)

        # Scale back to original frame coordinates
        sx = ow / w
        sy = oh / h
        rx = int((x + margin_x) * sx)
        ry = int((y + margin_y) * sy)
        rw = int(bw * sx)
        rh = int(bh * sy)

        # Ignore if box is nearly the full frame (no real detection)
        if rw > ow * 0.85 and rh > oh * 0.85:
            return None

        return (rx, ry, rw, rh)
    except Exception:
        return None
