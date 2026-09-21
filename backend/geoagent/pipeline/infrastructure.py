"""OpenStreetMap infrastructure via Overpass, with a database-backed cache.

We never fabricate infrastructure. If every public Overpass mirror is down the
function returns an empty layer and the risk stage reports that honestly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import LineString, Point

from geoagent.config import settings
from geoagent.db.models import InfrastructureCache
from geoagent.db.session import db_session

log = logging.getLogger(__name__)

DEFAULT_CATEGORIES = ["pipeline", "power_line", "road", "railway"]
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
_HEADERS = {"User-Agent": f"{settings.APP_NAME}/2.0 (+https://github.com)"}


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"name": [], "type": [], "osm_id": []}, geometry=[], crs="EPSG:4326")


def _cache_key(bbox, categories) -> str:
    payload = json.dumps([round(v, 5) for v in bbox] + sorted(categories))
    return hashlib.sha1(payload.encode()).hexdigest()[:20]


def _build_query(bbox, categories, timeout: int) -> str:
    minx, miny, maxx, maxy = bbox
    bb = f"{miny},{minx},{maxy},{maxx}"
    parts = []
    if "pipeline" in categories:
        parts.append(f'way["man_made"="pipeline"]({bb});')
    if "power_line" in categories:
        parts += [f'way["power"="line"]({bb});', f'way["power"="minor_line"]({bb});']
    if "substation" in categories:
        parts += [f'way["power"="substation"]({bb});', f'node["power"="substation"]({bb});']
    if "road" in categories:
        parts.append(f'way["highway"~"^(motorway|trunk|primary|secondary)$"]({bb});')
    if "railway" in categories:
        parts.append(f'way["railway"="rail"]({bb});')
    return f"[out:json][timeout:{timeout}];({''.join(parts)});out geom qt;"


def _classify(tags: dict) -> str:
    if tags.get("man_made") == "pipeline":
        return "pipeline"
    if tags.get("power") in ("line", "minor_line"):
        return "power_line"
    if tags.get("power") == "substation":
        return "substation"
    if tags.get("railway") == "rail":
        return "railway"
    if "highway" in tags:
        return "road"
    return "other"


def _to_gdf(elements: list[dict]) -> gpd.GeoDataFrame:
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        kind = _classify(tags)
        name = tags.get("name") or f"{kind} #{el.get('id')}"
        if el.get("type") == "way" and el.get("geometry"):
            coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(coords) >= 2:
                rows.append({"name": name, "type": kind, "osm_id": el["id"], "geometry": LineString(coords)})
        elif el.get("type") == "node" and "lat" in el:
            rows.append({"name": name, "type": kind, "osm_id": el["id"], "geometry": Point(el["lon"], el["lat"])})
    return gpd.GeoDataFrame(rows, crs="EPSG:4326") if rows else _empty()


def _load_cached(key: str) -> gpd.GeoDataFrame | None:
    with db_session() as db:
        row = db.get(InfrastructureCache, key)
        if row is None:
            return None
        age = (datetime.now(timezone.utc) - row.fetched_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age > settings.OSM_CACHE_TTL_SECONDS or not Path(row.geojson_path).exists():
            db.delete(row)
            return None
        path = row.geojson_path
    try:
        return gpd.read_file(path) if Path(path).stat().st_size > 60 else _empty()
    except Exception:  # noqa: BLE001
        return None


def _store_cached(key: str, bbox, categories, gdf: gpd.GeoDataFrame, endpoint: str) -> None:
    path = settings.OSM_CACHE_DIR / f"{key}.geojson"
    if gdf.empty:
        path.write_text('{"type":"FeatureCollection","features":[]}')
    else:
        gdf.to_file(path, driver="GeoJSON")
    with db_session() as db:
        db.merge(InfrastructureCache(
            cache_key=key, minx=bbox[0], miny=bbox[1], maxx=bbox[2], maxy=bbox[3],
            categories=sorted(categories), feature_count=int(len(gdf)),
            source_endpoint=endpoint, geojson_path=str(path), fetched_at=datetime.now(timezone.utc),
        ))


def fetch_osm_infrastructure(bbox: tuple[float, float, float, float],
                             categories: list[str] | None = None,
                             timeout: int = 25) -> gpd.GeoDataFrame:
    categories = sorted(set(categories or DEFAULT_CATEGORIES))
    key = _cache_key(bbox, categories)

    cached = _load_cached(key)
    if cached is not None:
        return cached

    query = _build_query(bbox, categories, timeout)
    last_error: Exception | None = None
    for url in OVERPASS_MIRRORS:
        try:
            resp = requests.post(url, data={"data": query}, timeout=timeout + 5, headers=_HEADERS)
            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", "0") or 0), 5)
                log.warning("Overpass %s rate-limited (retry-after=%s)", url, wait)
                if wait:
                    time.sleep(wait)
                last_error = RuntimeError(f"HTTP 429 from {url}")
                continue
            resp.raise_for_status()
            gdf = _to_gdf(resp.json().get("elements", []))
            _store_cached(key, bbox, categories, gdf, url)
            return gdf
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.warning("Overpass %s failed: %s", url, exc)

    log.error("all Overpass mirrors failed: %s", last_error)
    if settings.ALLOW_SYNTHETIC_INFRASTRUCTURE:
        return synthetic_infrastructure(bbox)
    return _empty()


def synthetic_infrastructure(bbox, n_lines: int = 3) -> gpd.GeoDataFrame:
    """Demo-only geometry. Clearly labelled so it can't be mistaken for OSM data."""
    minx, miny, maxx, maxy = bbox
    lines = [
        LineString([(minx + (maxx - minx) * (0.2 + 0.2 * i), miny + (maxy - miny) * 0.1),
                    (minx + (maxx - minx) * (0.3 + 0.2 * i), maxy - (maxy - miny) * 0.1)])
        for i in range(n_lines)
    ]
    return gpd.GeoDataFrame(
        {"name": [f"SYNTHETIC pipeline {i}" for i in range(n_lines)], "type": "pipeline", "osm_id": [-1] * n_lines},
        geometry=lines, crs="EPSG:4326",
    )
