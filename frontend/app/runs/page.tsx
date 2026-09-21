"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtDate, RISK_COLOUR, type Run } from "@/lib/api";

const PAGE = 12;

export default function RunsPage() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = (p = page) => {
    setLoading(true);
    api
      .runs(PAGE, p * PAGE)
      .then((r) => {
        setRuns(r.items);
        setTotal(r.total);
        setErr(null);
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => load(page), [page]); // eslint-disable-line react-hooks/exhaustive-deps

  const remove = async (id: string) => {
    if (!confirm("Delete this run and its files?")) return;
    await api.deleteRun(id);
    load();
  };

  return (
    <div className="max-w-6xl w-full mx-auto px-5 py-8">
      <div className="flex items-end justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold">Run history</h1>
          <p className="text-sm text-fog-500 mt-1">
            {total} run{total === 1 ? "" : "s"} stored. Thumbnails are the recent Sentinel-2 composite each run used.
          </p>
        </div>
        <Link href="/" className="btn-primary">
          New run
        </Link>
      </div>

      {err && <p className="text-risk-high text-sm mb-4">{err}</p>}

      {!loading && runs.length === 0 && (
        <div className="panel p-10 text-center text-fog-500">
          No runs yet. <Link href="/" className="text-teal">Start one from the monitor</Link>.
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {runs.map((r) => (
          <RunCard key={r.id} run={r} onDelete={() => remove(r.id)} />
        ))}
      </div>

      {total > PAGE && (
        <div className="flex justify-center gap-2 mt-8 text-sm">
          <button className="btn-ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Previous
          </button>
          <span className="px-3 py-2 text-fog-500">
            {page + 1} / {Math.ceil(total / PAGE)}
          </span>
          <button className="btn-ghost" disabled={(page + 1) * PAGE >= total} onClick={() => setPage((p) => p + 1)}>
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function RunCard({ run, onDelete }: { run: Run; onDelete: () => void }) {
  const risk = run.risk_level ?? "NONE";
  const done = run.status === "completed";
  return (
    <article className="panel overflow-hidden flex flex-col">
      <Link href={`/runs/detail/?id=${run.id}`} className="relative block aspect-[4/3] bg-ink-900">
        {run.previews.t2 ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={run.previews.t2} alt="Recent composite" className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-fog-700 text-sm">
            {run.status === "failed" ? "Failed" : "Processing…"}
          </div>
        )}
        {run.previews.change && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={run.previews.change} alt="" className="absolute inset-0 w-full h-full object-cover opacity-90" />
        )}
        {done && (
          <span
            className="absolute top-2 left-2 text-[11px] font-medium px-2 py-0.5 rounded-full text-ink-900"
            style={{ background: RISK_COLOUR[risk] }}
          >
            {risk}
          </span>
        )}
        {run.is_mock && (
          <span className="absolute top-2 right-2 text-[11px] px-2 py-0.5 rounded-full bg-ink-900/80 text-fog-300">
            synthetic
          </span>
        )}
      </Link>
      <div className="p-3 flex-1 flex flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <Link href={`/runs/detail/?id=${run.id}`} className="font-medium truncate hover:text-teal">
            {run.label || run.run_key}
          </Link>
          <span className="text-[11px] text-fog-700 shrink-0">{fmtDate(run.created_at)}</span>
        </div>
        <div className="text-xs text-fog-500">
          {done ? (
            <>
              {run.changed_percent?.toFixed(2)}% changed · {run.n_change_polygons} areas · {run.n_near_infra} near infra
            </>
          ) : (
            <span className="capitalize">{run.status.replace("_", " ")}</span>
          )}
        </div>
        <div className="text-[11px] text-fog-700">
          {run.minx.toFixed(3)}, {run.miny.toFixed(3)} → {run.maxx.toFixed(3)}, {run.maxy.toFixed(3)}
        </div>
        <div className="mt-auto pt-2 flex justify-end">
          <button onClick={onDelete} className="text-[11px] text-fog-700 hover:text-risk-high">
            Delete
          </button>
        </div>
      </div>
    </article>
  );
}
