"""Face-aware crop-window selection using OpenCV Haar cascades.

Lazily-imported from PhotoProcessor. If OpenCV isn't available at runtime,
find_face_crop_box() returns None and callers fall back to center crop.
"""

import logging
from typing import List, Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

_DETECT_MAX_EDGE = 800
_FACE_PAD_FRACTION = 0.15

_cascade_cache: dict = {}


def _get_cascades():
    """Return (frontal, profile) classifiers, or (None, None) if unavailable.

    Loaded once per process.
    """
    if _cascade_cache.get('loaded'):
        return _cascade_cache.get('frontal'), _cascade_cache.get('profile')

    frontal = None
    profile = None
    try:
        import cv2
        frontal_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        profile_path = cv2.data.haarcascades + 'haarcascade_profileface.xml'
        frontal = cv2.CascadeClassifier(frontal_path)
        profile = cv2.CascadeClassifier(profile_path)
        if frontal.empty():
            logger.warning(f"Frontal face cascade empty at {frontal_path}")
            frontal = None
        if profile.empty():
            logger.warning(f"Profile face cascade empty at {profile_path}")
            profile = None
    except ImportError:
        logger.warning("OpenCV not available; smart crop disabled (center-crop fallback)")
    except Exception as e:
        logger.warning(f"Failed to load Haar cascades: {e}")

    _cascade_cache['loaded'] = True
    _cascade_cache['frontal'] = frontal
    _cascade_cache['profile'] = profile
    return frontal, profile


def _detect_faces(pil_img: Image.Image) -> List[Tuple[int, int, int, int]]:
    """Detect faces, returning (x, y, w, h) boxes in pil_img coordinates."""
    frontal, profile = _get_cascades()
    if frontal is None and profile is None:
        return []

    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    w, h = pil_img.size
    long_edge = max(w, h)
    if long_edge > _DETECT_MAX_EDGE:
        scale = _DETECT_MAX_EDGE / long_edge
        det_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        det_img = pil_img.resize(det_size, Image.BILINEAR)
    else:
        scale = 1.0
        det_img = pil_img

    gray = np.array(det_img.convert('L'))
    gray = cv2.equalizeHist(gray)

    min_dim = min(gray.shape[0], gray.shape[1])
    min_size_px = max(30, min_dim // 15)
    params = dict(
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(min_size_px, min_size_px),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )

    detections: List[Tuple[int, int, int, int]] = []

    if frontal is not None:
        for (x, y, fw, fh) in frontal.detectMultiScale(gray, **params):
            detections.append((int(x), int(y), int(fw), int(fh)))

    if profile is not None:
        for (x, y, fw, fh) in profile.detectMultiScale(gray, **params):
            detections.append((int(x), int(y), int(fw), int(fh)))
        # profileface is trained for one direction only; mirror to catch the other.
        flipped = cv2.flip(gray, 1)
        gw = gray.shape[1]
        for (x, y, fw, fh) in profile.detectMultiScale(flipped, **params):
            detections.append((int(gw - x - fw), int(y), int(fw), int(fh)))

    if not detections:
        return []

    if scale != 1.0:
        inv = 1.0 / scale
        detections = [
            (int(x * inv), int(y * inv), int(fw * inv), int(fh * inv))
            for (x, y, fw, fh) in detections
        ]

    return detections


def _union_box(detections: List[Tuple[int, int, int, int]]) -> Tuple[int, int, int, int]:
    x1 = min(d[0] for d in detections)
    y1 = min(d[1] for d in detections)
    x2 = max(d[0] + d[2] for d in detections)
    y2 = max(d[1] + d[3] for d in detections)
    return x1, y1, x2, y2


def _pad_box(box: Tuple[int, int, int, int], img_w: int, img_h: int,
             frac: float) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    pw = int((x2 - x1) * frac)
    ph = int((y2 - y1) * frac)
    return (
        max(0, x1 - pw),
        max(0, y1 - ph),
        min(img_w, x2 + pw),
        min(img_h, y2 + ph),
    )


def _pick_window_offset(img_dim: int, win_dim: int,
                        target_lo: int, target_hi: int) -> int:
    """Pick a window offset along one axis.

    Prefers offsets that fully contain [target_lo, target_hi]. If that's
    impossible (window smaller than target), centers on the target. Subject
    to that, biases toward image center.
    """
    max_offset = img_dim - win_dim
    if max_offset <= 0:
        return 0
    center = max_offset // 2

    lo = max(0, target_hi - win_dim)
    hi = min(max_offset, target_lo)

    if lo <= hi:
        return max(lo, min(hi, center))

    target_center = (target_lo + target_hi) // 2
    offset = target_center - win_dim // 2
    return max(0, min(max_offset, offset))


def find_face_crop_box(img: Image.Image,
                       target_ratio: float) -> Optional[Tuple[int, int, int, int]]:
    """Return a (left, top, right, bottom) crop box at `target_ratio`
    (width / height) that keeps detected faces in frame, or None when no
    faces are detected or OpenCV is unavailable.
    """
    if target_ratio <= 0:
        return None

    detections = _detect_faces(img)
    if not detections:
        logger.debug("Smart crop: no faces detected, caller should fall back")
        return None

    img_w, img_h = img.size
    face_union = _union_box(detections)
    padded = _pad_box(face_union, img_w, img_h, _FACE_PAD_FRACTION)

    img_ratio = img_w / img_h
    if target_ratio > img_ratio:
        win_w = img_w
        win_h = max(1, int(round(win_w / target_ratio)))
    else:
        win_h = img_h
        win_w = max(1, int(round(win_h * target_ratio)))
    win_w = min(img_w, win_w)
    win_h = min(img_h, win_h)

    px1, py1, px2, py2 = padded
    left = _pick_window_offset(img_w, win_w, px1, px2)
    top = _pick_window_offset(img_h, win_h, py1, py2)
    box = (left, top, left + win_w, top + win_h)
    logger.info(
        f"Smart crop: {len(detections)} face(s) found; "
        f"union={face_union} padded={padded} img=({img_w}x{img_h}) crop={box}"
    )
    return box
