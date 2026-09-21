"use client";

import Link from "next/link";
import { RISK_COLOUR, type Run } from "@/lib/api";

interface Props {
  run: Run;
  blend: number;
  onBlend: (v: number) => void;
  layers: { change: boolean; polygons: boolean; infra: boolean };
  onLayers: (l: Props["layers"]) => void;
}

export function ResultStrip({ run, blend, onBlend, layers, onLayers }: Props) {
  const risk = run.risk_level ?? "NONE";
  const colour = RISK_COLOUR[risk];

  return (
    <div className="panel px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-3">
      <div className="flex items-center gap-3">
        <span className="w-3 h-3 rounded-full" style={{ background: colour, boxShadow: `0 0 18px ${colour}` }} />
        <div>
          <div className="text-sm font-semibold leading-tight">Risk {risk.toLowerCase()}</div>
          <div className="text-[11px] text-fog-500">
            {run.is_mock ? "synthetic demo imagery" : `Sentinel-2 · latest ${run.t2_latest_date ?? "—"}`}
          </div>
        </div>
      </div>

      <Stat label="Changed" value={`${run.changed_percent?.toFixed(2) ?? "—"}%`} />
      <Stat label="Change areas" value={run.n_change_polygons ?? "—"} />
      <Stat label={`Within ${run.buffer_m} m`} value={run.n_near_infra ?? "—"} />
      <Stat label="Scenes" value={`${run.t1_scene_count ?? "—"} → ${run.t2_scene_count ?? "—"}`} />
      <Stat label="OSM features" value={run.infra_feature_count ?? "—"} />
      {run.duration_s != null && <Stat label="Runtime" value={`${run.duration_s} s`} />}

      <div className="flex items-center gap-2 text-xs ml-auto">
        <span className="text-fog-500">Before</span>
        <input
          type="range"
          className="!w-28"
          min={0}
          max={1}
          step={0.05}
          value={blend}
          onChange={(e) => onBlend(Number(e.target.value))}
          aria-label="Blend baseline and recent imagery"
        />
        <span className="text-fog-500">After</span>
      </div>

      <div className="flex items-center gap-1.5">
        <Toggle on={layers.change} onClick={() => onLayers({ ...layers, change: !layers.change })} label="Heat" />
        <Toggle on={layers.polygons} onClick={() => onLayers({ ...layers, polygons: !layers.polygons })} label="Areas" />
        <Toggle on={layers.infra} onClick={() => onLayers({ ...layers, infra: !layers.infra })} label="Infrastructure" />
      </div>

      <Link href={`/runs/${run.id}`} className="btn-ghost !py-1.5 text-xs">
        Full report
      </Link>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="leading-tight">
      <div className="text-sm font-medium">{value}</div>
      <div className="text-[11px] text-fog-500">{label}</div>
    </div>
  );
}

function Toggle({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={`text-xs px-2 py-1 rounded border transition ${
        on ? "border-amber/60 text-amber bg-amber/10" : "border-white/10 text-fog-500 hover:text-fog-100"
      }`}
    >
      {label}
    </button>
  );
}
