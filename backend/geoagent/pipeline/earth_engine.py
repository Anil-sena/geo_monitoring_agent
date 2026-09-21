"""Sentinel-1 / Sentinel-2 retrieval through Google Earth Engine.

Sentinel-2 is not a live feed. Each request resolves to the newest scenes in
the requested window and we always report the acquisition dates that were
actually used so the UI can show them next to the result.
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import rasterio
from rasterio.merge import merge as rio_merge

from geoagent.config import settings

log = logging.getLogger(__name__)

S2_BANDS = ["B4", "B3", "B2", "B8"]  # R, G, B, NIR at 10 m
S1_BANDS = ["VV", "VH"]

_init_lock = threading.Lock()
_initialised = False


def initialize_ee() -> None:
    """Initialise EE once per process. Raises a readable error on failure."""
    global _initialised
    if _initialised:
        return
    with _init_lock:
        if _initialised:
            return
        import ee

        try:
            if settings.EE_SERVICE_ACCOUNT and settings.EE_PRIVATE_KEY_FILE:
                creds = ee.ServiceAccountCredentials(
                    settings.EE_SERVICE_ACCOUNT, str(settings.EE_PRIVATE_KEY_FILE)
                )
                ee.Initialize(creds, project=settings.EE_PROJECT)
            elif settings.EE_PROJECT:
                ee.Initialize(project=settings.EE_PROJECT)
            else:
                ee.Initialize()
            ee.Number(1).getInfo()  # cheap round-trip to prove auth works
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Earth Engine initialisation failed. Run `earthengine authenticate` "
                "and make sure EE_PROJECT is an Earth-Engine-registered Cloud project. "
                f"({exc})"
            ) from exc
        _initialised = True
        log.info("Earth Engine ready (project=%s)", settings.EE_PROJECT)


def sentinel2_collection(aoi, start: str, end: str):
    import ee

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", settings.S2_CLOUD_PCT))
    )
    cloud_score = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    clear = settings.S2_CLEAR_THRESHOLD
    return (
        s2.linkCollection(cloud_score, ["cs_cdf"])
        .map(lambda img: img.updateMask(img.select("cs_cdf").gte(clear)))
        .select(["B2", "B3", "B4", "B8", "B11", "B12"])
        .map(lambda img: img.divide(10000.0).copyProperties(img, ["system:time_start"]))
    )


def sentinel1_collection(aoi, start: str, end: str):
    import ee

    return (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .select(S1_BANDS)
    )


def _scene_dates(collection) -> list[str]:
    millis = collection.aggregate_array("system:time_start").getInfo() or []
    return sorted({datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for ms in millis})


TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
ZIP_MAGIC = b"PK\x03\x04"


def _fetch(url: str, dest: Path, attempts: int = 3) -> Path:
    """Download to disk with a couple of retries on transient network errors."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=300) as resp, dest.open("wb") as fh:
                shutil.copyfileobj(resp, fh, length=1 << 20)
            return dest
        except Exception as exc:  # noqa: BLE001
            last = exc
            dest.unlink(missing_ok=True)
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"download failed after {attempts} attempts: {last}")


def _describe_payload(path: Path) -> str:
    """Earth Engine reports quota/geometry problems as an HTML or JSON body."""
    try:
        text = path.read_bytes()[:800].decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        return "unreadable response"
    if '"error"' in text:
        try:
            return json.loads(path.read_text("utf-8", "replace"))["error"]["message"]
        except Exception:  # noqa: BLE001
            pass
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())[:300] or "empty response"


def _repack(sources: list[rasterio.io.DatasetReader], out_path: Path) -> None:
    """Write one clean, tiled, deflate-compressed GeoTIFF from open band readers."""
    profile = sources[0].profile.copy()
    profile.update(
        driver="GTiff",
        count=sum(src.count for src in sources),
        dtype=sources[0].dtypes[0],
        tiled=True,
        compress="deflate",
        predictor=2,
        photometric="MINISBLACK",
        interleave="band",
    )
    profile.pop("nodata", None)
    with rasterio.open(out_path, "w", **profile) as dst:
        idx = 1
        for src in sources:
            for band in range(1, src.count + 1):
                dst.write(src.read(band), idx)
                idx += 1


def _download_tile(image, region: list, out_path: Path, scale: int, bands: list[str]) -> Path:
    """Download one tile from Earth Engine and normalise it to a clean GeoTIFF.

    getDownloadURL returns different things depending on the format: GEO_TIFF
    gives a single multi-band TIFF, the zipped formats give an archive of
    per-band TIFFs, and any failure gives an HTML or JSON error body with a 200
    status. Sniff the magic bytes rather than assuming, so a quota message
    surfaces as a readable error instead of "File is not a zip file".
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = out_path.with_suffix(".part")
    extract_dir = out_path.parent / f".{out_path.stem}_bands"

    url = image.select(bands).getDownloadURL(
        {"region": region, "scale": scale, "crs": "EPSG:4326", "format": "GEO_TIFF"}
    )
    _fetch(url, payload)

    try:
        head = payload.open("rb").read(4)

        if head.startswith(TIFF_MAGIC):
            # Single multi-band TIFF: bands come back in the order we selected.
            with rasterio.open(payload) as src:
                _repack([src], out_path)

        elif head.startswith(ZIP_MAGIC):
            with zipfile.ZipFile(payload) as zf:
                members = [m for m in zf.namelist() if m.lower().endswith((".tif", ".tiff"))]
                if not members:
                    raise RuntimeError(f"Earth Engine archive held no GeoTIFF for {out_path.name}")
                extract_dir.mkdir(parents=True, exist_ok=True)
                zf.extractall(extract_dir)

            def order(member: str) -> int:
                stem = Path(member).stem.lower()
                for i, b in enumerate(bands):
                    if stem == b.lower() or stem.endswith(("_" + b.lower(), "." + b.lower())):
                        return i
                return len(bands)

            members.sort(key=order)
            sources = [rasterio.open(extract_dir / m) for m in members]
            try:
                _repack(sources, out_path)
            finally:
                for src in sources:
                    src.close()

        else:
            raise RuntimeError(f"Earth Engine returned an error instead of imagery: {_describe_payload(payload)}")

    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        payload.unlink(missing_ok=True)

    return out_path


def _is_size_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("too large", "request size", "limit", "exceed", "pixel", "memory"))


def export_geotiff(image, bbox: tuple[float, float, float, float], out_path: Path, scale: int, bands: list[str]) -> Path:
    """Download a possibly large AOI as a grid of tiles and merge locally.

    getDownloadURL is capped at ~48 MiB per request; a city-sized AOI at 10 m
    blows through that, so we tile. Grid size scales with the estimated payload.
    """
    minx, miny, maxx, maxy = bbox
    out_path = Path(out_path)

    width_px = max(1, int((maxx - minx) * 111_320 / scale))
    height_px = max(1, int((maxy - miny) * 111_320 * math.cos(math.radians((miny + maxy) / 2)) / scale))
    est_mb = width_px * height_px * len(bands) * 4 / (1024 ** 2)
    grid = int(math.ceil(math.sqrt(max(1.0, est_mb / 24.0))))
    grid = min(max(grid, 1), 8)

    tiles: list[Path] = []
    for row in range(grid):
        for col in range(grid):
            x1 = minx + (maxx - minx) * col / grid
            x2 = minx + (maxx - minx) * (col + 1) / grid
            y1 = miny + (maxy - miny) * row / grid
            y2 = miny + (maxy - miny) * (row + 1) / grid
            region = [[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]
            tile = out_path.parent / f".{out_path.stem}_{row}_{col}.tif"
            try:
                _download_tile(image, region, tile, scale, bands)
            except Exception as exc:  # noqa: BLE001
                if not _is_size_error(exc):
                    raise
                # Over the request-size cap: retry this tile coarser instead of
                # failing the whole AOI.
                log.warning("tile %d/%d exceeded the request limit at %s m – retrying at %s m",
                            row, col, scale, min(scale * 2, 30))
                _download_tile(image, region, tile, min(scale * 2, 30), bands)
            tiles.append(tile)

    if grid == 1:
        tiles[0].replace(out_path)
        return out_path

    srcs = [rasterio.open(p) for p in tiles]
    try:
        mosaic, transform = rio_merge(srcs)
        meta = srcs[0].meta.copy()
        meta.update(height=mosaic.shape[1], width=mosaic.shape[2], transform=transform,
                    count=mosaic.shape[0], tiled=True, compress="deflate")
        with rasterio.open(out_path, "w", **meta) as dst:
            dst.write(mosaic)
    finally:
        for s in srcs:
            s.close()
        for p in tiles:
            p.unlink(missing_ok=True)
    log.info("exported %s (%dx%d tiles)", out_path.name, grid, grid)
    return out_path


def fetch_image_pair(
    bbox: tuple[float, float, float, float],
    t1: tuple[str, str],
    t2: tuple[str, str],
    out_dir: Path,
    sensor: str = "S2",
    scale: int = 10,
) -> dict[str, Any]:
    """Build two cloud-masked median composites and download them.

    Returns evidence metadata (scene counts/dates) plus local file paths.
    """
    import ee

    initialize_ee()
    minx, miny, maxx, maxy = bbox
    aoi = ee.Geometry.Rectangle([minx, miny, maxx, maxy])
    sensor = sensor.upper()

    if sensor == "S2":
        col1, col2 = sentinel2_collection(aoi, *t1), sentinel2_collection(aoi, *t2)
        bands = S2_BANDS
    else:
        col1, col2 = sentinel1_collection(aoi, *t1), sentinel1_collection(aoi, *t2)
        bands = S1_BANDS

    n1, n2 = col1.size().getInfo(), col2.size().getInfo()
    if n1 == 0 or n2 == 0:
        raise ValueError(
            f"Not enough imagery: older window has {n1} scenes, recent window has {n2}. "
            "Widen the windows or relax the cloud filter."
        )

    dates1, dates2 = _scene_dates(col1), _scene_dates(col2)
    out_dir = Path(out_dir)
    p1 = export_geotiff(col1.median(), bbox, out_dir / "t1.tif", scale, bands)
    p2 = export_geotiff(col2.median(), bbox, out_dir / "t2.tif", scale, bands)

    return {
        "sensor": sensor,
        "bands": bands,
        "scale_m": scale,
        "t1_window": f"{t1[0]}/{t1[1]}",
        "t2_window": f"{t2[0]}/{t2[1]}",
        "scene_count": {"t1": n1, "t2": n2},
        "scene_dates": {"t1": dates1, "t2": dates2},
        "latest_available": {"t1": dates1[-1] if dates1 else None, "t2": dates2[-1] if dates2 else None},
        "paths": {"t1": str(p1), "t2": str(p2)},
        "is_mock": False,
    }
