"use client";

import { useEffect, useState } from "react";
import { api, STAGES, stageIndex, type Preset, type Run, type RunParams } from "@/lib/api";

interface Props {
  params: RunParams;
  onChange: (p: RunParams) => void;
  drawing: boolean;
  onToggleDraw: () => void;
  onLaunch: () => void;
  launching: boolean;
  activeRun: Run | null;
  error: string | null;
  onClose?: () => void;
}

export function MissionPanel({ params, onChange, drawing, onToggleDraw, onLaunch, launching, activeRun, error, onClose }: Props) {
  const [presets, setPresets] = useState<Preset[]>([]);

  useEffect(() => {
    api.presets().then(setPresets).catch(() => {});
  }, []);

  const set = <K extends keyof RunParams>(k: K, v: RunParams[K]) => onChange({ ...params, [k]: v });
  const num = (k: keyof RunParams) => (e: React.ChangeEvent<HTMLInputElement>) =>
    set(k, Number(e.target.value) as never);

  const areaKm2 =
    (params.maxx - params.minx) * 111.32 * Math.cos(((params.miny + params.maxy) / 2) * (Math.PI / 180)) *
    (params.maxy - params.miny) * 111.32;

  const busy = activeRun && activeRun.status !== "completed" && activeRun.status !== "failed";

  return (
    <aside className="panel w-[340px] max-w-[90vw] flex flex-col overflow-hidden">
      <div className="px-4 pt-4 pb-3 border-b border-white/5 flex items-start gap-2">
        <div className="flex-1">
          <h2 className="text-base font-semibold">Area and settings</h2>
          <p className="text-xs text-fog-500 mt-0.5">
            The agent uses whatever is set here. You can also run the pipeline directly.
          </p>
        </div>
        {onClose && (
          <button onClick={onClose} aria-label="Close" className="text-fog-500 hover:text-fog-100 text-lg leading-none">
            ×
          </button>
        )}
      </div>

      <div className="px-4 py-3 space-y-4 overflow-y-auto">
        {/* AOI */}
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="field-label mb-0">Area of interest</label>
            <button
              type="button"
              onClick={onToggleDraw}
              className={`text-xs px-2 py-1 rounded border transition ${
                drawing ? "border-teal text-teal bg-teal/10" : "border-white/10 text-fog-300 hover:bg-white/5"
              }`}
            >
              {drawing ? "Click two corners…" : "Draw on map"}
            </button>
          </div>
          <select
            className="field"
            defaultValue=""
            onChange={(e) => {
              const p = presets.find((x) => x.id === e.target.value);
              if (p) onChange({ ...params, minx: p.minx, miny: p.miny, maxx: p.maxx, maxy: p.maxy, label: p.name });
            }}
          >
            <option value="" disabled>
              Load a saved area…
            </option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <div className="grid grid-cols-2 gap-2">
            <Coord label="West" value={params.minx} onChange={num("minx")} />
            <Coord label="East" value={params.maxx} onChange={num("maxx")} />
            <Coord label="South" value={params.miny} onChange={num("miny")} />
            <Coord label="North" value={params.maxy} onChange={num("maxy")} />
          </div>
          <p className="text-[11px] text-fog-700">≈ {areaKm2.toFixed(0)} km² at 10 m resolution</p>
        </section>

        {/* windows */}
        <section className="space-y-3">
          <Slider
            label="Baseline window ends"
            value={params.days_back_t1}
            min={30}
            max={180}
            step={5}
            suffix="days ago"
            onChange={num("days_back_t1")}
          />
          <Slider
            label="Recent window ends"
            value={params.days_back_t2}
            min={2}
            max={45}
            step={1}
            suffix="days ago"
            onChange={num("days_back_t2")}
          />
          <Slider
            label="Composite length"
            value={params.window_days}
            min={7}
            max={45}
            step={1}
            suffix="days"
            onChange={num("window_days")}
          />
        </section>

        {/* detection */}
        <section className="space-y-3">
          <Slider
            label="Change threshold"
            value={params.threshold}
            min={0.1}
            max={0.8}
            step={0.05}
            onChange={num("threshold")}
          />
          <Slider
            label="Infrastructure proximity"
            value={params.buffer_m}
            min={50}
            max={1000}
            step={50}
            suffix="m"
            onChange={num("buffer_m")}
          />
        </section>

        <label className="block">
          <span className="field-label">Label (optional)</span>
          <input
            className="field"
            value={params.label ?? ""}
            placeholder="e.g. Refinery north perimeter"
            onChange={(e) => set("label", e.target.value)}
          />
        </label>
      </div>

      <div className="px-4 py-3 border-t border-white/5 space-y-3">
        <button
          type="button"
          className="btn-primary w-full"
          onClick={onLaunch}
          disabled={launching || !!busy || params.days_back_t1 <= params.days_back_t2}
        >
          {busy ? "Running…" : "Run pipeline now"}
        </button>
        {params.days_back_t1 <= params.days_back_t2 && (
          <p className="text-xs text-risk-high">The baseline window must end before the recent window.</p>
        )}
        {error && <p className="text-xs text-risk-high">{error}</p>}
        {activeRun && <StageList run={activeRun} />}
      </div>
    </aside>
  );
}

function Coord({ label, value, onChange }: { label: string; value: number; onChange: React.ChangeEventHandler<HTMLInputElement> }) {
  return (
    <label className="block">
      <span className="field-label">{label}</span>
      <input className="field" type="number" step="0.0001" value={value} onChange={onChange} />
    </label>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix?: string;
  onChange: React.ChangeEventHandler<HTMLInputElement>;
}) {
  return (
    <label className="block">
      <span className="flex justify-between text-xs mb-1">
        <span className="text-fog-500">{label}</span>
        <span className="text-fog-100">
          {step < 1 ? value.toFixed(2) : value} {suffix ?? ""}
        </span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={onChange} />
    </label>
  );
}

function StageList({ run }: { run: Run }) {
  const idx = stageIndex(run.status);
  const failed = run.status === "failed";
  return (
    <ol className="space-y-1.5 text-xs">
      {STAGES.map((s, i) => {
        const done = i < idx || run.status === "completed";
        const current = i === idx && !done;
        return (
          <li key={s.key} className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full shrink-0 ${
                failed && current ? "bg-risk-high" : done ? "bg-risk-none" : current ? "bg-amber pulse" : "bg-ink-500"
              }`}
            />
            <span className={done || current ? "text-fog-100" : "text-fog-700"}>{s.label}</span>
            {done && run.timings && timingFor(s.key, run.timings) && (
              <span className="ml-auto text-fog-700">{timingFor(s.key, run.timings)}</span>
            )}
          </li>
        );
      })}
      {failed && run.error && <li className="text-risk-high pt-1 break-words">{run.error}</li>}
    </ol>
  );
}

function timingFor(stage: string, t: Record<string, number>): string | null {
  const key = { fetching_imagery: "imagery_s", detecting_change: "change_s", assessing_risk: "risk_s" }[stage];
  return key && t[key] != null ? `${t[key]} s` : null;
}
