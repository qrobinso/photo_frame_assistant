"""Post-hoc clipping detection for rendered frame images.

When a frame's saved contrast/saturation adjustments push an image beyond
what the pipeline can cleanly represent, shadows get crushed to 0 and
highlights blown to 255. This module measures that directly on the
rendered output and returns scaled-back boost values the caller can use
to re-render once.

The frame's saved settings are never modified.
"""

import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

CLIPPED_FRACTION_THRESHOLD = 0.05
OVERSHOOT_SCALE = 0.5

_SAMPLE_MAX_EDGE = 400


def clipped_fraction(img: Image.Image) -> float:
    """Return fraction (0-1) of pixels where any RGB channel is <=2 or >=253.

    Downsamples to ~400px long-edge for speed; clipping stats are
    essentially sample-invariant at that resolution.
    """
    sample = img.copy()
    sample.thumbnail((_SAMPLE_MAX_EDGE, _SAMPLE_MAX_EDGE), Image.BILINEAR)
    if sample.mode != 'RGB':
        sample = sample.convert('RGB')
    arr = np.asarray(sample)
    clipped = ((arr <= 2) | (arr >= 253)).any(axis=-1)
    return float(clipped.mean())


def scale_boosts(contrast_factor: float, saturation: int, factor: float):
    """Scale the distance-from-identity portion of each boost by `factor`.

    Only applies to the boost side. Reductions (contrast < 1.0 or
    saturation < 100) pass through unchanged, since halving a
    desaturation would undo a deliberate choice.

    Examples:
        scale_boosts(1.5, 130, 0.5) -> (1.25, 115)
        scale_boosts(0.8, 130, 0.5) -> (0.80, 115)
        scale_boosts(1.5,  90, 0.5) -> (1.25,  90)
    """
    new_contrast = contrast_factor
    if contrast_factor > 1.0:
        new_contrast = 1.0 + (contrast_factor - 1.0) * factor

    new_saturation = saturation
    if saturation > 100:
        new_saturation = int(round(100 + (saturation - 100) * factor))

    return new_contrast, new_saturation
