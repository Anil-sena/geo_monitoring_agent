"use client";

import { useEffect, useMemo, useState } from "react";
import {
  GeoJSON,
  ImageOverlay,
  MapContainer,
  Rectangle,
  TileLayer,
  ZoomControl,
  useMap,
  useMapEvents,
} from "react-leaflet";
import L, { type LatLngBoundsExpression, type LeafletMouseEvent } from "leaflet";
import type { Bbox, Run } from "@/lib/api";
import { api, RISK_COLOUR } from "@/lib/api";

// Real imagery: Esri World Imagery orthophoto mosaic, labels from Carto.
const ESRI_IMAGERY =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTR =
  "Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community";
const CARTO_LABELS = "https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png";
const CARTO_ATTR = "&copy; OpenStreetMap contributors &copy; CARTO";

export interface MissionMapProps {
  bbox: Bbox;
  onBboxChange?: (b: Bbox) => void;
  drawing?: boolean;
  onDrawEnd?: () => void;
  run?: Run | null;
  /** 0 = baseline only … 1 = recent only */
  blend?: number;
  showChange?: boolean;
  showPolygons?: boolean;
  showInfra?: boolean;
}

const toBounds = (b: Bbox): LatLngBoundsExpression => [
  [b.miny, b.minx],
  [b.maxy, b.maxx],
];

function FitToBbox({ bbox }: { bbox: Bbox }) {
  const map = useMap();
  const key = `${bbox.minx},${bbox.miny},${bbox.maxx},${bbox.maxy}`;
  useEffect(() => {
    map.fitBounds(toBounds(bbox), { padding: [40, 40], animate: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}

/** Two clicks define a new AOI. */
function DrawBbox({
  active,
  onChange,
  onDone,
}: {
  active: boolean;
  onChange: (b: Bbox) => void;
  onDone: () => void;
}) {
  const [first, setFirst] = useState<[number, number] | null>(null);
  const [cursor, setCursor] = useState<[number, number] | null>(null);

  useMapEvents({
    click(e: LeafletMouseEvent) {
      if (!active) return;
      const pt: [number, number] = [e.latlng.lng, e.latlng.lat];
      if (!first) {
        setFirst(pt);
        return;
      }
      onChange({
        minx: Math.min(first[0], pt[0]),
        maxx: Math.max(first[0], pt[0]),
        miny: Math.min(first[1], pt[1]),
        maxy: Math.max(first[1], pt[1]),
      });
      setFirst(null);
      setCursor(null);
      onDone();
    },
    mousemove(e: LeafletMouseEvent) {
      if (active && first) setCursor([e.latlng.lng, e.latlng.lat]);
    },
  });

  useEffect(() => {
    if (!active) {
      setFirst(null);
      setCursor(null);
    }
  }, [active]);

  if (!first || !cursor) return null;
  return (
    <Rectangle
      bounds={[
        [Math.min(first[1], cursor[1]), Math.min(first[0], cursor[0])],
        [Math.max(first[1], cursor[1]), Math.max(first[0], cursor[0])],
      ]}
      pathOptions={{ color: "#3fc1c9", weight: 1.5, dashArray: "6 4", fillOpacity: 0.08 }}
    />
  );
}

export default function MissionMap({
  bbox,
  onBboxChange,
  drawing = false,
  onDrawEnd,
  run,
  blend = 1,
  showChange = true,
  showPolygons = true,
  showInfra = true,
}: MissionMapProps) {
  const [polys, setPolys] = useState<GeoJSON.FeatureCollection | null>(null);
  const [infra, setInfra] = useState<GeoJSON.FeatureCollection | null>(null);

  useEffect(() => {
    setPolys(null);
    setInfra(null);
    if (!run || run.status !== "completed") return;
    let alive = true;
    api.polygons(run.id).then((fc) => alive && setPolys(fc)).catch(() => {});
    api.infrastructure(run.id).then((fc) => alive && setInfra(fc)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [run?.id, run?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const runBounds = useMemo(
    () => (run ? toBounds({ minx: run.minx, miny: run.miny, maxx: run.maxx, maxy: run.maxy }) : null),
    [run],
  );
  const centre: [number, number] = [(bbox.miny + bbox.maxy) / 2, (bbox.minx + bbox.maxx) / 2];

  return (
    <div className={`h-full w-full ${drawing ? "map-crosshair" : ""}`}>
      <MapContainer center={centre} zoom={11} className="h-full w-full" zoomControl={false} preferCanvas>
        <TileLayer url={ESRI_IMAGERY} attribution={ESRI_ATTR} maxZoom={19} />
        <TileLayer url={CARTO_LABELS} attribution={CARTO_ATTR} maxZoom={19} opacity={0.85} />
        <FitToBbox bbox={bbox} />

        {onBboxChange && onDrawEnd && <DrawBbox active={drawing} onChange={onBboxChange} onDone={onDrawEnd} />}

        <Rectangle
          bounds={toBounds(bbox)}
          pathOptions={{ color: "#3fc1c9", weight: 2, fill: false, dashArray: drawing ? "6 4" : undefined }}
          interactive={false}
        />

        {run && runBounds && run.previews.t1 && blend < 1 && (
          <ImageOverlay url={run.previews.t1} bounds={runBounds} opacity={1 - blend} zIndex={400} />
        )}
        {run && runBounds && run.previews.t2 && blend > 0 && (
          <ImageOverlay url={run.previews.t2} bounds={runBounds} opacity={blend} zIndex={401} />
        )}
        {run && runBounds && showChange && run.previews.change && (
          <ImageOverlay url={run.previews.change} bounds={runBounds} opacity={0.9} zIndex={410} />
        )}

        {showInfra && infra && infra.features.length > 0 && (
          <GeoJSON
            key={`infra-${run?.id}`}
            data={infra}
            style={(f) => ({
              color: infraColour(f?.properties?.type),
              weight: 2,
              opacity: 0.85,
            })}
            pointToLayer={(_f, latlng) => L.circleMarker(latlng, { radius: 4, color: "#b7c5d1", fillOpacity: 0.8 })}
            onEachFeature={(f, layer) =>
              layer.bindTooltip(`${f.properties?.type ?? "infrastructure"}: ${f.properties?.name ?? ""}`)
            }
          />
        )}

        {showPolygons && polys && polys.features.length > 0 && (
          <GeoJSON
            key={`polys-${run?.id}`}
            data={polys}
            style={(f) => {
              const s = f?.properties?.risk_score ?? 0;
              const c = s > 0.7 ? RISK_COLOUR.HIGH : s > 0.4 ? RISK_COLOUR.MEDIUM : RISK_COLOUR.LOW;
              return { color: c, weight: 2, fillColor: c, fillOpacity: 0.25 };
            }}
            onEachFeature={(f, layer) => {
              const p = f.properties ?? {};
              const ha = p.area_m2 ? (p.area_m2 / 10000).toFixed(2) : "?";
              const dist =
                p.distance_to_infra_m != null
                  ? `${Math.round(p.distance_to_infra_m)} m from ${p.nearest_infra_type ?? "infrastructure"}`
                  : "no infrastructure mapped";
              layer.bindPopup(
                `<strong>Change area</strong><br/>${ha} ha · risk ${Number(p.risk_score ?? 0).toFixed(2)}<br/>${dist}`,
              );
            }}
          />
        )}

        <ZoomControl position="bottomright" />
      </MapContainer>
    </div>
  );
}

function infraColour(type?: string): string {
  switch (type) {
    case "pipeline":
      return "#ff7ab6";
    case "power_line":
      return "#ffd166";
    case "railway":
      return "#c9c9c9";
    case "road":
      return "#8ecae6";
    default:
      return "#b7c5d1";
  }
}
