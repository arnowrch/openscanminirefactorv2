"""
Focus stacking via ECC alignment + Laplacian variance pixel selection.

Requires opencv-python-headless. If not installed, stack_jpegs() returns
the middle frame without error so the rest of the pipeline keeps working.
"""

import io
import logging

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    from PIL import Image
    _OPENCV_OK = True
except ImportError:
    _OPENCV_OK = False
    logger.info("focus_stacker: opencv/PIL not installed — stacking disabled, passthrough mode")


class FocusStacker:
    def __init__(self, downscale: float = 0.25, jpeg_quality: int = 90):
        self.downscale = downscale
        self.jpeg_quality = jpeg_quality

    @property
    def available(self) -> bool:
        return _OPENCV_OK

    def stack_jpegs(self, jpegs: list[bytes]) -> bytes:
        """
        Align and merge a focus bracket into one sharp image.

        Falls back to the middle frame when opencv is unavailable or
        alignment fails on all images.
        """
        if not jpegs:
            raise ValueError("stack_jpegs: empty list")

        if len(jpegs) == 1 or not _OPENCV_OK:
            return jpegs[len(jpegs) // 2]

        # Decode to RGB numpy arrays
        try:
            imgs = [
                np.array(Image.open(io.BytesIO(j)).convert("RGB"))
                for j in jpegs
            ]
        except Exception as e:
            logger.warning(f"stack_jpegs decode failed: {e} — returning middle frame")
            return jpegs[len(jpegs) // 2]

        ref_idx = len(imgs) // 2
        ref = imgs[ref_idx]
        ref_gray = self._to_gray_f32(ref, scale=self.downscale)

        aligned: list[np.ndarray] = []
        for i, img in enumerate(imgs):
            if i == ref_idx:
                aligned.append(img)
                continue
            gray = self._to_gray_f32(img, scale=self.downscale)
            warp = np.eye(2, 3, dtype=np.float32)
            criteria = (
                cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                20,
                1e-6,
            )
            try:
                _, warp = cv2.findTransformECC(
                    ref_gray, gray, warp, cv2.MOTION_AFFINE, criteria,
                    inputMask=None, gaussFiltSize=3
                )
                # Scale translation back from downscaled space
                warp[:, 2] /= self.downscale
                warped = cv2.warpAffine(
                    img, warp, (img.shape[1], img.shape[0]),
                    flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                )
                aligned.append(warped)
            except cv2.error:
                aligned.append(img)  # keep unaligned on ECC failure

        # Merge: pick sharpest pixel per position via Laplacian variance map
        result = aligned[0].copy().astype(np.float32)
        best_focus = self._focus_map(aligned[0])

        for img in aligned[1:]:
            fmap = self._focus_map(img)
            mask = (fmap > best_focus)[..., np.newaxis]
            result = np.where(mask, img.astype(np.float32), result)
            best_focus = np.where(fmap > best_focus, fmap, best_focus)

        # Encode
        merged = np.clip(result, 0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(merged).save(buf, format="JPEG", quality=self.jpeg_quality)
        return buf.getvalue()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _to_gray_f32(self, img: "np.ndarray", scale: float = 1.0) -> "np.ndarray":
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if scale != 1.0:
            gray = cv2.resize(gray, None, fx=scale, fy=scale)
        return gray.astype(np.float32) / 255.0

    def _focus_map(self, img: "np.ndarray") -> "np.ndarray":
        """Per-pixel Laplacian energy, upscaled to full resolution."""
        h, w = img.shape[:2]
        gray = self._to_gray_f32(img, scale=self.downscale)
        lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
        energy = lap * lap
        return cv2.resize(energy, (w, h))


# Module-level singleton
_stacker: FocusStacker | None = None


def get_focus_stacker(downscale: float = 0.25, jpeg_quality: int = 90) -> FocusStacker:
    global _stacker
    if _stacker is None:
        _stacker = FocusStacker(downscale=downscale, jpeg_quality=jpeg_quality)
    return _stacker
