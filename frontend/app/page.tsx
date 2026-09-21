"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { Conversation } from "@/components/Conversation";
import { MissionPanel } from "@/components/MissionPanel";
import { ResultStrip } from "@/components/ResultStrip";
import { api, type Run, type RunParams } from "@/lib/api";

const MissionMap = dynamic(() => import("@/components/MissionMap"), {
  ssr: false,
  loading: () => <div className="h-full w-full bg-ink-900" />,
});

const DEFAULT_PARAMS: RunParams = {
  minx: 83.15,
  miny: 17.65,
  maxx: 83.35,
  maxy: 17.8,
  days_back_t1: 60,
  days_back_t2: 15,
  window_days: 20,
  threshold: 0.35,
  buffer_m: 200,
  label: "Visakhapatnam (small)",
};

export default function ConsolePage() {
  const [params, setParams] = useState<RunParams>(DEFAULT_PARAMS);
  const [run, setRun] = useState<Run | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blend, setBlend] = useState(1);
  const [layers, setLayers] = useState({ change: true, polygons: true, infra: true });
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    api
      .runs(1)
      .then(({ items }) => {
        const last = items[0];
        if (last?.status === "completed") {
          setRun(last);
          setParams((p) => ({ ...p, minx: last.minx, miny: last.miny, maxx: last.maxx, maxy: last.maxy }));
        }
      })
      .catch(() => {});
  }, []);

  const follow = useCallback((id: string) => {
    source.current?.close();
    const refresh = () => api.run(id).then(setRun).catch(() => {});
    const es = new EventSource(api.eventsUrl(id));
    source.current = es;
    es.addEventListener("status", (ev) => {
      const { status } = JSON.parse((ev as MessageEvent).data);
      setRun((r) => (r && r.id === id ? { ...r, status } : r));
      if (status === "completed" || status === "failed") {
        es.close();
        refresh();
      }
    });
    es.onerror = () => {
      es.close();
      const t = setInterval(async () => {
        const r = await api.run(id).catch(() => null);
        if (r) setRun(r);
        if (!r || r.status === "completed" || r.status === "failed") clearInterval(t);
      }, 2500);
    };
  }, []);

  useEffect(() => () => source.current?.close(), []);

  // A run the agent started from chat: adopt it and watch it finish.
  const adoptRun = useCallback(
    (id: string) => {
      api
        .run(id)
        .then((r) => {
          setRun(r);
          setBlend(1);
          if (r.status !== "completed" && r.status !== "failed") follow(id);
        })
        .catch(() => {});
    },
    [follow],
  );

  const launch = async () => {
    setError(null);
    setLaunching(true);
    try {
      const created = await api.createRun(params);
      setRun(created);
      setBlend(1);
      setControlsOpen(false);
      follow(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the run");
    } finally {
      setLaunching(false);
    }
  };

  const busy = run && run.status !== "completed" && run.status !== "failed";

  return (
    <div className="relative flex-1 min-h-0">
      <div className="absolute inset-0">
        <MissionMap
          bbox={params}
          onBboxChange={(b) => setParams((p) => ({ ...p, ...b }))}
          drawing={drawing}
          onDrawEnd={() => setDrawing(false)}
          run={run}
          blend={blend}
          showChange={layers.change}
          showPolygons={layers.polygons}
          showInfra={layers.infra}
        />
      </div>

      {/* the conversation is the primary surface */}
      <div className="absolute top-4 left-4 bottom-4 w-[380px] max-w-[92vw] z-[500]">
        <Conversation aoi={params} onRun={adoptRun} onOpenControls={() => setControlsOpen(true)} />
      </div>

      {busy && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[500] panel px-4 py-2 text-xs flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-amber pulse" />
          <span className="capitalize">{run!.status.replace(/_/g, " ")}</span>
        </div>
      )}

      {drawing && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[500] panel px-3 py-1.5 text-xs">
          Click the first corner, then the opposite corner
        </div>
      )}

      {run?.status === "completed" && (
        <div className="absolute left-[412px] right-4 bottom-4 z-[500] max-[1100px]:left-4">
          <ResultStrip run={run} blend={blend} onBlend={setBlend} layers={layers} onLayers={setLayers} />
        </div>
      )}

      {run?.status === "failed" && (
        <div className="absolute left-[412px] right-4 bottom-4 z-[500] panel px-4 py-3 text-sm max-[1100px]:left-4">
          <span className="text-risk-high font-medium">Run failed.</span>{" "}
          <span className="text-fog-500">{run.error}</span>
        </div>
      )}

      {/* manual controls sit behind a drawer — the agent is the default path */}
      {controlsOpen && (
        <>
          <button
            aria-label="Close settings"
            className="fixed inset-0 z-[600] bg-ink-900/60"
            onClick={() => setControlsOpen(false)}
          />
          <div className="absolute top-4 right-4 bottom-4 z-[700] flex">
            <MissionPanel
              params={params}
              onChange={setParams}
              drawing={drawing}
              onToggleDraw={() => {
                setDrawing((d) => !d);
                setControlsOpen(false);
              }}
              onLaunch={launch}
              launching={launching}
              activeRun={run}
              error={error}
              onClose={() => setControlsOpen(false)}
            />
          </div>
        </>
      )}
    </div>
  );
}
