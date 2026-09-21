"""Change detection.

Two families:
  * SpectralChangeDetector — training-free, fast, what the API uses by default.
    Combines per-band reflectance difference with NDVI difference, standardises
    the result with a robust (median/MAD) z-score and cleans speckle with a
    morphological opening. Because the score is standardised, the same threshold
    means the same thing on a quiet desert scene and a busy urban one — the old
    min-max scaling always produced a "100 % change" pixel somewhere, even on
    identical images.
  * Torch models (Siamese U-Net lite, TorchGeo FCSiamDiff, SMP U-Net) for when a
    trained checkpoint is available. torch is imported lazily so the default path
    starts in well under a second.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from skimage import morphology
from skimage.transform import resize

log = logging.getLogger(__name__)


def _ndvi(img: np.ndarray) -> np.ndarray:
    red, nir = img[0].astype(np.float32), img[3].astype(np.float32)
    return (nir - red) / (nir + red + 1e-6)


class SpectralChangeDetector:
    def __init__(self, threshold: float = 0.35, min_object_px: int = 4, ndvi_weight: float = 0.5):
        self.threshold = threshold
        self.min_object_px = min_object_px
        self.ndvi_weight = ndvi_weight

    def predict(self, img1: np.ndarray, img2: np.ndarray) -> dict[str, Any]:
        img1, img2 = _align(img1, img2)
        valid = np.isfinite(img1).all(axis=0) & np.isfinite(img2).all(axis=0)
        valid &= (img1 != 0).any(axis=0) & (img2 != 0).any(axis=0)

        a = np.nan_to_num(img1.astype(np.float32))
        b = np.nan_to_num(img2.astype(np.float32))

        spectral = np.abs(a - b).mean(axis=0)
        if a.shape[0] >= 4:
            veg = np.abs(_ndvi(b) - _ndvi(a))
            # NDVI lives on a bigger scale than reflectance deltas; bring them together.
            magnitude = (1 - self.ndvi_weight) * spectral / (spectral[valid].std() + 1e-6) \
                + self.ndvi_weight * veg / (veg[valid].std() + 1e-6)
        else:
            magnitude = spectral / (spectral[valid].std() + 1e-6)

        # Robust z-score: median/MAD so a few genuine changes don't drag the baseline.
        med = np.median(magnitude[valid]) if valid.any() else 0.0
        mad = np.median(np.abs(magnitude[valid] - med)) * 1.4826 if valid.any() else 1.0
        z = (magnitude - med) / (mad + 1e-6)
        prob = 1.0 / (1.0 + np.exp(-(z - 3.0)))  # 3 MAD above baseline -> 0.5
        prob[~valid] = 0.0

        mask = prob > self.threshold
        mask = morphology.opening(mask, morphology.disk(1)).astype(bool)
        mask = _drop_small(mask, self.min_object_px)

        return {
            "change_prob": prob.astype(np.float32),
            "change_mask": mask.astype(np.uint8),
            "valid_mask": valid,
            "method": "spectral",
            "threshold": self.threshold,
        }


def _drop_small(mask: np.ndarray, min_px: int) -> np.ndarray:
    # skimage renamed min_size -> max_size in 0.26 (and made it inclusive)
    try:
        return morphology.remove_small_objects(mask, max_size=min_px - 1)
    except TypeError:
        return morphology.remove_small_objects(mask, min_size=min_px)


def _align(img1: np.ndarray, img2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if img1.ndim == 3 and img1.shape[0] > img1.shape[-1]:  # HWC -> CHW
        img1, img2 = np.moveaxis(img1, -1, 0), np.moveaxis(img2, -1, 0)
    if img1.shape != img2.shape:
        h = min(img1.shape[1], img2.shape[1])
        w = min(img1.shape[2], img2.shape[2])
        img1, img2 = img1[:, :h, :w], img2[:, :h, :w]
    return img1, img2


# --- deep-learning path ------------------------------------------------------

def _torch():
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Deep-learning change detection needs `pip install torch`.") from exc
    return torch, nn, F


def build_siamese_lite(in_channels: int = 4, base: int = 16):
    torch, nn, _ = _torch()

    class SiameseUNetLite(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Conv2d(in_channels, base, 3, padding=1), nn.ReLU(inplace=True),
                nn.Conv2d(base, base * 2, 3, stride=2, padding=1), nn.ReLU(inplace=True),
                nn.Conv2d(base * 2, base * 4, 3, stride=2, padding=1), nn.ReLU(inplace=True),
            )
            self.decoder = nn.Sequential(
                nn.ConvTranspose2d(base * 8, base * 2, 2, stride=2), nn.ReLU(inplace=True),
                nn.ConvTranspose2d(base * 2, base, 2, stride=2), nn.ReLU(inplace=True),
                nn.Conv2d(base, 1, 1),
            )

        def forward(self, x1, x2):
            return torch.sigmoid(self.decoder(torch.cat([self.encoder(x1), self.encoder(x2)], dim=1)))

    return SiameseUNetLite()


def load_model(method: str = "spectral", threshold: float = 0.35, checkpoint: Path | None = None, device: str = "cpu"):
    if method in ("spectral", "simple"):
        return SpectralChangeDetector(threshold=threshold)

    torch, _, _ = _torch()
    if method == "siamese_lite":
        model = build_siamese_lite()
    elif method == "fcsiam_diff":
        try:
            from torchgeo.models import FCSiamDiff
        except ImportError:
            log.warning("torchgeo not installed; using siamese_lite instead")
            return load_model("siamese_lite", threshold, checkpoint, device)
        model = FCSiamDiff(in_channels=4, classes=1)
    elif method == "smp_unet":
        import segmentation_models_pytorch as smp
        model = smp.Unet(encoder_name="resnet18", encoder_weights="imagenet", in_channels=4, classes=1, activation="sigmoid")
    else:
        raise ValueError(f"unknown change detection method '{method}'")

    if checkpoint and Path(checkpoint).exists():
        model.load_state_dict(torch.load(checkpoint, map_location=device))
    else:
        log.warning("%s has no trained checkpoint — predictions will be near random", method)
    return model.to(device).eval()


def run_inference(model, img1: np.ndarray, img2: np.ndarray, device: str = "cpu") -> dict[str, Any]:
    if isinstance(model, SpectralChangeDetector):
        return model.predict(img1, img2)

    torch, _, F = _torch()
    img1, img2 = _align(img1, img2)
    x1 = torch.from_numpy(np.nan_to_num(img1).astype(np.float32)).unsqueeze(0).to(device)
    x2 = torch.from_numpy(np.nan_to_num(img2).astype(np.float32)).unsqueeze(0).to(device)

    h, w = x1.shape[-2:]
    nh, nw = ((h + 31) // 32) * 32, ((w + 31) // 32) * 32
    if (nh, nw) != (h, w):
        x1 = F.interpolate(x1, size=(nh, nw), mode="bilinear", align_corners=False)
        x2 = F.interpolate(x2, size=(nh, nw), mode="bilinear", align_corners=False)

    with torch.no_grad():
        name = type(model).__name__
        if name == "SiameseUNetLite":
            out = model(x1, x2)
        elif name == "FCSiamDiff":
            out = torch.sigmoid(model(torch.stack([x1, x2], dim=1)))
        else:
            out = model(torch.cat([x1, x2], dim=1)[:, :4])

    prob = out.squeeze().cpu().numpy()
    if prob.shape != (h, w):
        prob = resize(prob, (h, w), order=1, preserve_range=True)
    mask = _drop_small(prob > 0.5, 4)
    return {"change_prob": prob.astype(np.float32), "change_mask": mask.astype(np.uint8),
            "method": name, "threshold": 0.5}
