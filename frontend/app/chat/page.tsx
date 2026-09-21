"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, type ChatMessage, type Health, type Preset, type RunParams } from "@/lib/api";

const SESSION_KEY = "geoagent.chat.session";

const STARTERS = [
  "Monitor the selected area for construction or land-cover change near pipelines and power lines.",
  "Compare the last two weeks with two months ago and tell me which change areas are closest to roads.",
  "Summarise the most recent run and explain what the risk score means.",
];

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [aoi, setAoi] = useState<RunParams | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    api.presets().then((p) => {
      setPresets(p);
      if (p[0]) setAoi(toParams(p[0]));
    }).catch(() => {});
    const saved = typeof window !== "undefined" ? localStorage.getItem(SESSION_KEY) : null;
    if (saved) {
      setSessionId(saved);
      api.chatHistory(saved).then(setMessages).catch(() => localStorage.removeItem(SESSION_KEY));
    }
  }, []);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [messages, busy]);

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    setInput("");
    setBusy(true);
    const optimistic: ChatMessage = { id: -Date.now(), role: "user", content: text, run_id: null, latency_ms: null, created_at: new Date().toISOString() };
    setMessages((m) => [...m, optimistic]);
    try {
      const res = await api.chat({ message: text, session_id: sessionId ?? undefined, aoi: aoi ?? undefined });
      if (!sessionId) {
        setSessionId(res.session_id);
        localStorage.setItem(SESSION_KEY, res.session_id);
      }
      setMessages((m) => [
        ...m,
        { id: res.message_id, role: "assistant", content: res.answer, run_id: res.run_ids.at(-1) ?? null, latency_ms: res.latency_ms, created_at: new Date().toISOString() },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { id: -Date.now(), role: "assistant", content: `Request failed: ${e instanceof Error ? e.message : e}`, run_id: null, latency_ms: null, created_at: new Date().toISOString() },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    localStorage.removeItem(SESSION_KEY);
    setSessionId(null);
    setMessages([]);
  };

  return (
    <div className="flex-1 min-h-0 grid lg:grid-cols-[300px_1fr]">
      <aside className="border-r border-white/5 bg-ink-800/60 p-5 space-y-5 overflow-y-auto">
        <div>
          <h1 className="text-base font-semibold">Analyst chat</h1>
          <p className="text-xs text-fog-500 mt-1">
            {health?.llm_configured
              ? `Agent running on ${health.llm_provider}. It can launch runs, read past runs and explain results.`
              : "No LLM key configured — messages run the deterministic pipeline on the selected area instead."}
          </p>
        </div>

        <label className="block">
          <span className="field-label">Area the agent should use</span>
          <select className="field" value={aoi?.label ?? ""} onChange={(e) => {
            const p = presets.find((x) => x.name === e.target.value);
            if (p) setAoi(toParams(p));
          }}>
            {presets.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
          </select>
        </label>
        {aoi && (
          <p className="text-[11px] text-fog-700">
            {aoi.minx.toFixed(3)}, {aoi.miny.toFixed(3)} → {aoi.maxx.toFixed(3)}, {aoi.maxy.toFixed(3)}
          </p>
        )}

        <div className="space-y-2">
          <span className="field-label">Try asking</span>
          {STARTERS.map((s) => (
            <button key={s} onClick={() => send(s)} className="block w-full text-left text-xs p-2.5 rounded-md bg-white/5 hover:bg-white/10 text-fog-300">
              {s}
            </button>
          ))}
        </div>

        <button onClick={reset} className="btn-ghost w-full text-xs">Start a new conversation</button>
      </aside>

      <section className="flex flex-col min-h-0">
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          {messages.length === 0 && (
            <div className="max-w-xl text-fog-500 text-sm">
              Ask for an area to be monitored, or about a previous run. Answers cite the satellite dates and
              scene counts actually used; Sentinel-2 is latest-available imagery, not live.
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[75%] rounded-2xl px-4 py-3 text-sm ${
                m.role === "user" ? "bg-teal/15 text-fog-100 rounded-br-sm" : "panel rounded-bl-sm text-fog-300"
              }`}>
                {m.role === "user" ? m.content : <div className="prose-geo"><ReactMarkdown>{m.content}</ReactMarkdown></div>}
                {(m.run_id || m.latency_ms != null) && (
                  <div className="mt-2 flex items-center gap-3 text-[11px] text-fog-700">
                    {m.run_id && <Link href={`/runs/${m.run_id}`} className="text-teal hover:underline">Open run on map</Link>}
                    {m.latency_ms != null && <span>{(m.latency_ms / 1000).toFixed(1)} s</span>}
                  </div>
                )}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="panel rounded-2xl rounded-bl-sm px-4 py-3 text-sm text-fog-500">
                <span className="pulse">Working — Earth Engine queries can take a minute…</span>
              </div>
            </div>
          )}
          <div ref={bottom} />
        </div>

        <form
          onSubmit={(e) => { e.preventDefault(); send(input); }}
          className="border-t border-white/5 p-4 flex gap-2"
        >
          <input
            className="field flex-1"
            placeholder="Ask about the selected area…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
          />
          <button type="submit" className="btn-primary" disabled={busy || !input.trim()}>Send</button>
        </form>
      </section>
    </div>
  );
}

function toParams(p: Preset): RunParams {
  return { minx: p.minx, miny: p.miny, maxx: p.maxx, maxy: p.maxy, days_back_t1: 60, days_back_t2: 15, window_days: 20, threshold: 0.35, buffer_m: 200, label: p.name };
}
