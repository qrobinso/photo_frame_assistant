"""Face-aware crop-window selection using OpenCV YuNet DNN detector.

Uses cv2.FaceDetectorYN with a vendored ONNX model at
helpers/models/face_detection_yunet_2023mar.onnx. Falls back gracefully
(returns None from find_face_crop_box) when OpenCV or the model file is
unavailable, letting callers fall back to center crop.
"""

import logging
import os
from typing import List, Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

_DETECT_MAX_EDGE = 800
_FACE_PAD_FRACTION = 0.15

_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'models',
    'face_detection_yunet_2023mar.onnx',
)
# YuNet thresholds. Lowered from the 0.9 default to catch more faces
# in group shots and off-angle faces; NMS handles duplicates.
_SCORE_THRESHOLD = 0.6
_NMS_THRESHOLD = 0.3
_TOP_K = 5000

_detector_cache: dict = {}


def _get_detector():
    """Return a cached cv2.FaceDetectorYN instance, or None if unavailable."""
    if _detector_cache.get('loaded'):
        return _detector_cache.get('detector')

    detector = None
    try:
        import cv2
        if not os.path.isfile(_MODEL_PATH):
            logger.warning(
                f"YuNet ONNX model missing at {_MODEL_PATH}; "
                f"smart crop disabled (center-crop fallback)"
            )
        else:
            # Input size is set per-image via setInputSize below; the
            # constructor's input_size is a placeholder.
            detector = cv2.FaceDetectorYN_create(
                model=_MODEL_PATH,
                config='',
                input_size=(320, 320),
                score_threshold=_SCORE_THRESHOLD,
                nms_threshold=_NMS_THRESHOLD,
                top_k=_TOP_K,
            )
    except ImportError:
        logger.warning("OpenCV not available; smart crop disabled (center-crop fallback)")
    except Exception as e:
        logger.warning(f"Failed to initialize YuNet detector: {e}")

    _detector_cache['loaded'] = True
    _detector_cache['detector'] = detector
    return detector


def _detect_faces(pil_img: Image.Image) -> List[Tuple[int, int, int, int]]:
    """Detect faces with YuNet. Returns (x, y, w, h) boxes in pil_img coords."""
    detector = _get_detector()
    if detector is None:
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
        det_w = max(1, int(w * scale))
        det_h = max(1, int(h * scale))
        det_img = pil_img.resize((det_w, det_h), Image.BILINEAR)
    else:
        scale = 1.0
        det_w, det_h = w, h
        det_img = pil_img

    if det_img.mode != 'RGB':
        det_img = det_img.convert('RGB')
    # YuNet expects BGR (OpenCV convention).
    bgr = cv2.cvtColor(np.asarray(det_img), cv2.COLOR_RGB2BGR)

    detector.setInputSize((det_w, det_h))
    retval, faces = detector.detect(bgr)
    if faces is None or len(faces) == 0:
        return []

    detections: List[Tuple[int, int, int, int]] = []
    inv = 1.0 / scale if scale != 1.0 else 1.0
    for row in faces:
        x, y, fw, fh = row[0], row[1], row[2], row[3]
        # Clamp into the detection image; scale back to full-res coords.
        x1 = max(0.0, float(x))
        y1 = max(0.0, float(y))
        x2 = min(float(det_w), x1 + float(fw))
        y2 = min(float(det_h), y1 + float(fh))
        if x2 <= x1 or y2 <= y1:
            continue
        detections.append((
            int(round(x1 * inv)),
            int(round(y1 * inv)),
            int(round((x2 - x1) * inv)),
            int(round((y2 - y1) * inv)),
        ))
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
    """Center the window on [target_lo, target_hi], clamped to image bounds.

    Returns an offset in [0, img_dim - win_dim] such that the midpoint of
    [target_lo, target_hi] sits as close to the center of the window as
    possible. When the target is near an edge, the window slides up against
    that edge instead of being centered on the image.
    """
    max_offset = img_dim - win_dim
    if max_offset <= 0:
        return 0
    target_center = (target_lo + target_hi) / 2
    offset = int(round(target_center - win_dim / 2))
    return max(0, min(max_offset, offset))


def find_face_crop_box(img: Image.Image,
                       target_ratio: float) -> Optional[Tuple[int, int, int, int]]:
    """Return a (left, top, right, bottom) crop box at `target_ratio`
    (width / height) centered on detected faces, or None when no faces are
    detected or the detector is unavailable.
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
        f"Smart crop (YuNet): {len(detections)} face(s) found; "
        f"union={face_union} padded={padded} img=({img_w}x{img_h}) crop={box}"
    )
    return box
