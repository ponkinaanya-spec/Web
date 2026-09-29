"""Shared train/inference preprocessing. No rotation, crop or mirroring."""
from dataclasses import dataclass
import hashlib
import warnings

import numpy as np
from PIL import Image
import pydicom

PREPROCESSING = "percentile_1_99_uint8_letterbox_uniform_v1"
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


class ImageRejected(ValueError):
    pass


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    size: int = 224

    @property
    def scale(self):
        return min(self.size / self.width, self.size / self.height)

    @property
    def padding(self):
        s = self.scale
        return (self.size - self.width*s)/2, (self.size - self.height*s)/2

    def to_original(self, xy):
        # Pixel-center convention, inverse of the Pillow pixel-edge transform.
        px, py = self.padding
        return (np.asarray(xy) - [px, py] - (self.scale - 1)/2) / self.scale


def pixel_hash(raw):
    raw = np.asarray(raw)
    return hashlib.sha256(str(raw.shape).encode() + str(raw.dtype).encode()
                          + np.ascontiguousarray(raw).tobytes()).hexdigest()


def read_dicom(path_or_stream, max_pixels=32_000_000):
    with warnings.catch_warnings():
        # Malformed anonymized UIDs are recorded as a flag, not repaired.
        warnings.filterwarnings("ignore", message="Invalid value for VR UI.*")
        ds = pydicom.dcmread(path_or_stream)
    rows, cols = int(ds.get("Rows", 0)), int(ds.get("Columns", 0))
    if not rows or not cols or rows*cols > max_pixels:
        raise ImageRejected("invalid_or_excessive_image_dimensions")
    if int(ds.get("SamplesPerPixel", 1)) != 1 or int(ds.get("NumberOfFrames", 1)) != 1:
        raise ImageRejected("only_single_frame_grayscale_supported")
    if str(ds.get("PhotometricInterpretation", "")) not in ("MONOCHROME1", "MONOCHROME2"):
        raise ImageRejected("unsupported_photometric_interpretation")
    raw = np.asarray(ds.pixel_array)
    if raw.ndim != 2:
        raise ImageRejected("only_two_dimensional_images_supported")
    return ds, raw


def normalize(ds, raw):
    image = raw.astype(np.float32)
    image = image*float(ds.get("RescaleSlope", 1)) + float(ds.get("RescaleIntercept", 0))
    if not np.isfinite(image).all():
        raise ImageRejected("nonfinite_pixels")
    if str(ds.get("PhotometricInterpretation", "")) == "MONOCHROME1":
        image = image.max() - image
    lo, hi = np.percentile(image, [1, 99])
    if hi <= lo:
        raise ImageRejected("insufficient_contrast")
    return np.clip((image-lo)/(hi-lo), 0, 1).astype(np.float32)


def letterbox(image, size=224):
    if size <= 0 or int(size) != size or image.ndim != 2:
        raise ValueError("Invalid image/size")
    g = Geometry(image.shape[1], image.shape[0], int(size))
    # Same quantization order as torchvision.ToPILImage(float32) in 03/04/08.
    pil = Image.fromarray((image*255).astype(np.uint8))
    px, py = g.padding
    s = g.scale
    output = pil.transform((size, size), Image.Transform.AFFINE,
                           (1/s, 0, -px/s, 0, 1/s, -py/s),
                           resample=Image.Resampling.BILINEAR, fillcolor=0)
    return np.asarray(output).copy(), g


def image_tensor(images):
    import torch
    x = torch.as_tensor(np.asarray(images).copy(), dtype=torch.float32)/255
    if x.ndim == 2:
        x = x.unsqueeze(0)
    x = x.unsqueeze(1).repeat(1, 3, 1, 1)
    return (x - torch.tensor(MEAN)[None, :, None, None]) / torch.tensor(STD)[None, :, None, None]


def metadata_hints(ds):
    """Hints only. In particular PatientOrientation is NOT hip laterality."""
    body = str(ds.get("BodyPartExamined", "")).strip().upper()
    anatomy = {"LSPINE": "spine", "LUMBARSPINE": "spine", "HIP": "hip"}.get(body)
    sides = {str(ds.get(k, "")).strip().upper() for k in ("ImageLaterality", "Laterality")}
    sides.discard("")
    side = {"L": "left", "R": "right"}.get(next(iter(sides))) if len(sides) == 1 else None
    return {"anatomy_hint": anatomy, "laterality_hint": side,
            "laterality_conflict": len(sides) > 1}
