"""Render lightweight PNGs from the run's GeoTIFFs once, at the end of a run.

The dashboard overlays these on the map as image layers, so serving them is a
static-file read rather than a raster decode per request.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from geoagent.pipeline.geo import read_geotiff

MAX_SIDE = 1600


def _stretch(band: np.ndarray, lo_pct=2, hi_pct=98) -> np.ndarray:
    finite = band[np.isfinite(band) & (band != 0)]
    if finite.size == 0:
        return np.zeros_like(band, dtype=np.uint8)
    lo, hi = np.percentile(finite, [lo_pct, hi_pct])
    out = np.clip((band - lo) / (hi - lo + 1e-6), 0, 1)
    return (np.nan_to_num(out) * 255).astype(np.uint8)


def _downscale(img: Image.Image) -> Image.Image:
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return img


def true_colour_png(tif: Path, out: Path) -> Path:
    data, _ = read_geotiff(tif)
    if data.shape[0] >= 3:
        rgb = np.dstack([_stretch(data[i].astype(np.float32)) for i in range(3)])
        alpha = ((data[:3] != 0).any(axis=0) & np.isfinite(data[:3]).all(axis=0)).astype(np.uint8) * 255
        img = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    else:  # SAR: VV as grey
        g = _stretch(data[0].astype(np.float32))
        img = Image.fromarray(np.dstack([g, g, g, np.full_like(g, 255)]), "RGBA")
    _downscale(img).save(out, optimize=True)
    return out


def change_heatmap_png(prob_tif: Path, mask_tif: Path, out: Path) -> Path:
    prob, _ = read_geotiff(prob_tif)
    mask, _ = read_geotiff(mask_tif)
    p = np.clip(np.nan_to_num(prob[0]), 0, 1)
    m = mask[0] > 0

    # amber -> red ramp, transparent where nothing changed
    r = np.full(p.shape, 255, dtype=np.uint8)
    g = (200 * (1 - p)).astype(np.uint8)
    b = np.zeros(p.shape, dtype=np.uint8)
    a = np.where(m, 200, (p > 0.15) * (p * 120)).astype(np.uint8)
    img = Image.fromarray(np.dstack([r, g, b, a]), "RGBA")
    _downscale(img).save(out, optimize=True)
    return out


def build_previews(run_dir: Path, preview_dir: Path) -> dict[str, str]:
    preview_dir.mkdir(parents=True, exist_ok=True)
    made: dict[str, str] = {}
    for name in ("t1", "t2"):
        tif = run_dir / f"{name}.tif"
        if tif.exists():
            made[name] = str(true_colour_png(tif, preview_dir / f"{name}.png"))
    prob, mask = run_dir / "change_prob.tif", run_dir / "change_mask.tif"
    if prob.exists() and mask.exists():
        made["change"] = str(change_heatmap_png(prob, mask, preview_dir / "change.png"))
    return made
