"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, fmtDate, fmtHa, RISK_COLOUR, type Run } from "@/lib/api";

const MissionMap = dynamic(() => import("@/components/MissionMap"), { ssr: false });

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [blend, setBlend] = useState(1);
  const [layers, setLayers] = useState({ change: true, polygons: true, infra: true });

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    const load = () =>
      api
        .run(id)
        .then((r) => {
          setRun(r);
          if ((r.status === "completed" || r.status === "failed") && timer) clearInterval(timer);
        })
        .catch((e) => setErr(e.message));
    load();
    timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [id]);

  if (err) return <p className="p-8 text-risk-high">{err}</p>;
  if (!run) return <p className="p-8 text-fog-500">Loading run…</p>;

  const risk = run.risk_level ?? "NONE";
  const files = ["report.md", "change_polygons.geojson", "infrastructure.geojson", "change_mask.tif", "change_prob.tif", "t1.tif", "t2.tif"];

  return (
    <div className="flex-1 min-h-0 grid lg:grid-cols-[1fr_420px]">
      <div className="relative min-h-[420px]">
        <MissionMap
          bbox={run}
          run={run}
          blend={blend}
          showChange={layers.change}
          showPolygons={layers.polygons}
          showInfra={layers.infra}
        />
        <div className="absolute left-4 bottom-4 z-[500] panel px-3 py-2 flex items-center gap-3 text-xs">
          <span className="text-fog-500">Before</span>
          <input type="range" className="!w-32" min={0} max={1} step={0.05} value={blend} onChange={(e) => setBlend(Number(e.target.value))} />
          <span className="text-fog-500">After</span>
          <span className="w-px h-4 bg-white/10" />
          {(["change", "polygons", "infra"] as const).map((k) => (
            <button
              key={k}
              onClick={() => setLayers({ ...layers, [k]: !layers[k] })}
              className={`px-2 py-0.5 rounded border ${layers[k] ? "border-amber/60 text-amber" : "border-white/10 text-fog-500"}`}
            >
              {k === "change" ? "Heat" : k === "polygons" ? "Areas" : "Infrastructure"}
            </button>
          ))}
        </div>
      </div>

      <aside className="border-l border-white/5 bg-ink-800/60 overflow-y-auto">
        <div className="p-5 border-b border-white/5">
          <Link href="/runs" className="text-xs text-fog-500 hover:text-teal">← All runs</Link>
          <h1 className="text-lg font-semibold mt-2">{run.label || run.run_key}</h1>
          <p className="text-xs text-fog-500">
            {fmtDate(run.created_at)} · {run.requested_by} · {run.status.replace("_", " ")}
          </p>
          {run.status === "completed" && (
            <div className="mt-3 flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: RISK_COLOUR[risk] }} />
              <span className="font-medium">Risk {risk.toLowerCase()}</span>
              {run.is_mock && <span className="text-[11px] px-2 py-0.5 rounded-full bg-white/10 text-fog-300">synthetic imagery</span>}
            </div>
          )}
          {run.error && <p className="mt-3 text-sm text-risk-high break-words">{run.error}</p>}
        </div>

        {run.status === "completed" && (
          <>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 p-5 text-sm border-b border-white/5">
              <Row k="Baseline window" v={run.t1_window} />
              <Row k="Recent window" v={run.t2_window} />
              <Row k="Baseline scenes" v={`${run.t1_scene_count} (latest ${run.t1_latest_date ?? "—"})`} />
              <Row k="Recent scenes" v={`${run.t2_scene_count} (latest ${run.t2_latest_date ?? "—"})`} />
              <Row k="Changed pixels" v={`${run.changed_percent?.toFixed(2)} %`} />
              <Row k="Change areas" v={run.n_change_polygons} />
              <Row k={`Within ${run.buffer_m} m`} v={run.n_near_infra} />
              <Row k="OSM features" v={run.infra_feature_count} />
              <Row k="Threshold" v={run.threshold} />
              <Row k="Runtime" v={run.duration_s != null ? `${run.duration_s} s` : "—"} />
            </dl>

            {run.narrative && (
              <div className="p-5 border-b border-white/5 text-sm text-fog-300 prose-geo">
                <ReactMarkdown>{run.narrative}</ReactMarkdown>
              </div>
            )}

            {run.polygons.length > 0 && (
              <div className="p-5 border-b border-white/5">
                <h2 className="text-sm font-medium mb-2">Change areas by risk</h2>
                <table className="w-full text-xs">
                  <thead className="text-fog-500">
                    <tr className="text-left">
                      <th className="py-1 font-normal">Area</th>
                      <th className="font-normal">Risk</th>
                      <th className="font-normal">Nearest infrastructure</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...run.polygons].sort((a, b) => b.risk_score - a.risk_score).slice(0, 25).map((p) => (
                      <tr key={p.id} className="border-t border-white/5">
                        <td className="py-1.5">{fmtHa(p.area_m2)}</td>
                        <td>
                          <span className="inline-block w-1.5 h-1.5 rounded-full mr-1.5" style={{ background: p.risk_score > 0.7 ? RISK_COLOUR.HIGH : p.risk_score > 0.4 ? RISK_COLOUR.MEDIUM : RISK_COLOUR.LOW }} />
                          {p.risk_score.toFixed(2)}
                        </td>
                        <td className="text-fog-500">
                          {p.distance_to_infra_m != null ? `${Math.round(p.distance_to_infra_m)} m · ${p.nearest_infra_type}` : "none mapped"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="p-5">
              <h2 className="text-sm font-medium mb-2">Download</h2>
              <div className="flex flex-wrap gap-2">
                {files.map((f) => (
                  <a key={f} href={api.downloadUrl(run.id, f)} className="btn-ghost !py-1 text-xs">
                    {f}
                  </a>
                ))}
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] text-fog-500">{k}</dt>
      <dd className="text-fog-100">{v ?? "—"}</dd>
    </div>
  );
}
