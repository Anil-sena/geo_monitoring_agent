"""Raster I/O, vectorisation and infrastructure-proximity risk scoring."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features
from shapely.geometry import shape
from shapely.ops import unary_union

from geoagent.config import settings


def read_geotiff(path: Path) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as src:
        return src.read(), src.meta.copy()


def write_geotiff(array: np.ndarray, meta: dict, path: Path, dtype: str = "float32") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(meta, dtype=dtype, count=1 if array.ndim == 2 else array.shape[0],
                compress="deflate", tiled=True)
    with rasterio.open(path, "w", **meta) as dst:
        if array.ndim == 2:
            dst.write(array.astype(dtype), 1)
        else:
            dst.write(array.astype(dtype))
    return path


def bounds_from_meta(meta: dict) -> tuple[float, float, float, float]:
    t = meta["transform"]
    w, h = meta["width"], meta["height"]
    minx, maxy = t.c, t.f
    return (minx, maxy + h * t.e, minx + w * t.a, maxy)


def utm_crs_for(gdf: gpd.GeoDataFrame):
    """Local UTM zone — metres are accurate here, unlike Web Mercator at 17°N."""
    try:
        return gdf.estimate_utm_crs()
    except Exception:  # noqa: BLE001
        return "EPSG:3857"


def vectorize_change_mask(mask: np.ndarray, transform, crs="EPSG:4326",
                          min_area_m2: float | None = None) -> gpd.GeoDataFrame:
    min_area_m2 = settings.MIN_CHANGE_AREA_M2 if min_area_m2 is None else min_area_m2
    mask = mask.astype(np.uint8)
    geoms = [shape(g) for g, v in features.shapes(mask, mask=mask > 0, transform=transform) if v]
    if not geoms:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)

    gdf = gpd.GeoDataFrame({"geometry": geoms}, crs=crs)
    metric = gdf.to_crs(utm_crs_for(gdf))
    gdf["area_m2"] = metric.geometry.area.round(1)
    gdf = gdf[gdf["area_m2"] >= min_area_m2].reset_index(drop=True)
    return gdf


def compute_risk(change_gdf: gpd.GeoDataFrame, infra_gdf: gpd.GeoDataFrame | None,
                 buffer_m: float = 200.0) -> dict[str, Any]:
    """Score each change polygon by proximity to infrastructure and size.

    score = closeness × size, where closeness falls linearly from 1 (touching)
    to 0 at 3× the buffer distance, and size saturates at half a hectare.
    """
    empty_result = {
        "risk_level": "NONE", "n_change_polygons": 0, "n_near_infra": 0,
        "max_risk_score": 0.0, "buffer_m": buffer_m, "polygons": [],
    }
    if change_gdf.empty:
        return dict(empty_result, message="No change polygons above the minimum area were detected.")

    change_gdf = change_gdf.copy()
    crs = utm_crs_for(change_gdf)
    change_m = change_gdf.to_crs(crs)

    no_infra = infra_gdf is None or infra_gdf.empty
    if no_infra:
        change_gdf["risk_score"] = 0.0
        change_gdf["near_infra"] = False
        change_gdf["distance_to_infra_m"] = None
        change_gdf["nearest_infra_type"] = None
        change_gdf["nearest_infra_name"] = None
    else:
        infra_m = infra_gdf.to_crs(crs)
        infra_union = unary_union(infra_m.geometry.values)
        sindex = infra_m.sindex
        scores, near, dists, types, names = [], [], [], [], []
        for geom in change_m.geometry:
            dist = float(geom.distance(infra_union))
            closeness = max(0.0, 1.0 - dist / (buffer_m * 3))
            size = min(1.0, geom.area / 5000.0)
            scores.append(round(closeness * size, 3))
            near.append(dist <= buffer_m)
            dists.append(round(dist, 1))
            idx = list(sindex.nearest(geom, return_all=False))
            nearest = infra_m.iloc[idx[1][0]] if idx and len(idx[1]) else None
            types.append(None if nearest is None else nearest.get("type"))
            names.append(None if nearest is None else nearest.get("name"))
        change_gdf["risk_score"] = scores
        change_gdf["near_infra"] = near
        change_gdf["distance_to_infra_m"] = dists
        change_gdf["nearest_infra_type"] = types
        change_gdf["nearest_infra_name"] = names

    n_near = int(change_gdf["near_infra"].sum())
    max_score = float(change_gdf["risk_score"].max())
    if max_score > 0.7 or n_near >= 3:
        level = "HIGH"
    elif max_score > 0.4 or n_near >= 1:
        level = "MEDIUM"
    elif max_score > 0.1:
        level = "LOW"
    else:
        level = "NONE"

    if no_infra:
        message = (f"{len(change_gdf)} change areas detected, but OSM returned no infrastructure "
                   "for this AOI, so proximity risk could not be scored.")
    else:
        message = (f"{len(change_gdf)} change areas detected; {n_near} within {buffer_m:.0f} m "
                   f"of infrastructure. Risk level {level}.")

    centroids = change_gdf.to_crs(crs).geometry.centroid.to_crs("EPSG:4326")
    change_gdf["centroid_lon"] = centroids.x.round(6)
    change_gdf["centroid_lat"] = centroids.y.round(6)

    return {
        "risk_level": level,
        "n_change_polygons": int(len(change_gdf)),
        "n_near_infra": n_near,
        "max_risk_score": round(max_score, 3),
        "buffer_m": buffer_m,
        "message": message,
        "polygons": json.loads(change_gdf.to_json())["features"],
        "gdf": change_gdf,
    }


def save_geojson(gdf: gpd.GeoDataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")
    return path
