"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { ChatMessage, Health, RunParams } from "@/lib/api";
import { api } from "@/lib/api";

const SESSION_KEY = "geoagent.session";

const SUGGESTIONS = [
  "What changed here in the last two weeks?",
  "Find construction close to pipelines or power lines",
  "Compare this area against two months ago",
  "Explain the risk score on the last run",
];

interface Props {
  aoi: RunParams;
  onRun: (runId: string) => void;
  onOpenControls: () => void;
}

export function Conversation({ aoi, onRun, onOpenControls }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    const saved = localStorage.getItem(SESSION_KEY);
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
    const now = new Date().toISOString();
    setMessages((m) => [...m, { id: -Date.now(), role: "user", content: text, run_id: null, latency_ms: null, created_at: now }]);
    try {
      const res = await api.chat({ message: text, session_id: sessionId ?? undefined, aoi });
      if (!sessionId) {
        setSessionId(res.session_id);
        localStorage.setItem(SESSION_KEY, res.session_id);
      }
      setMessages((m) => [
        ...m,
        {
          id: res.message_id,
          role: "assistant",
          content: res.answer,
          run_id: res.run_ids.at(-1) ?? null,
          latency_ms: res.latency_ms,
          created_at: new Date().toISOString(),
        },
      ]);
      if (res.run_ids.length) onRun(res.run_ids[res.run_ids.length - 1]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          id: -Date.now(),
          role: "assistant",
          content: `That request didn't complete: ${e instanceof Error ? e.message : e}`,
          run_id: null,
          latency_ms: null,
          created_at: new Date().toISOString(),
        },
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
    <section className="panel flex flex-col h-full overflow-hidden">
      <header className="px-4 py-3 border-b border-white/5 flex items-start gap-2">
        <div className="flex-1">
          <h1 className="text-sm font-semibold">Ask about this area</h1>
          <p className="text-[11px] text-fog-500 mt-0.5">
            {health?.llm_configured
              ? "The agent pulls Sentinel-2 composites, detects change and scores it against nearby infrastructure."
              : "No model key configured — messages run the pipeline directly and return the report."}
          </p>
        </div>
        <button onClick={onOpenControls} className="text-[11px] text-fog-500 hover:text-teal shrink-0 mt-0.5">
          Settings
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 ? (
          <div className="space-y-4">
            <p className="text-sm text-fog-500">
              Currently looking at <span className="text-fog-100">{aoi.label || "the selected area"}</span>. Ask
              a question, or try one of these.
            </p>
            <div className="space-y-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="block w-full text-left text-xs p-2.5 rounded-lg bg-white/5 hover:bg-white/10 text-fog-300 transition"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) => <Bubble key={m.id} message={m} />)
        )}
        {busy && (
          <div className="text-xs text-fog-500 pulse">
            Working — Earth Engine composites usually take 30–90 seconds…
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="border-t border-white/5 p-3 space-y-2"
      >
        <div className="flex gap-2">
          <input
            className="field flex-1"
            placeholder="Ask anything about this area…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
          />
          <button type="submit" className="btn-primary" disabled={busy || !input.trim()}>
            Send
          </button>
        </div>
        {messages.length > 0 && (
          <button type="button" onClick={reset} className="text-[11px] text-fog-700 hover:text-fog-300">
            Clear conversation
          </button>
        )}
      </form>
    </section>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const mine = message.role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm ${
          mine ? "bg-teal/15 text-fog-100 rounded-br-sm" : "bg-white/5 text-fog-300 rounded-bl-sm"
        }`}
      >
        {mine ? (
          message.content
        ) : (
          <div className="prose-geo">
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </div>
        )}
        {(message.run_id || message.latency_ms != null) && (
          <div className="mt-2 flex items-center gap-3 text-[11px] text-fog-700">
            {message.run_id && (
              <Link href={`/runs/detail/?id=${message.run_id}`} className="text-teal hover:underline">
                Full report
              </Link>
            )}
            {message.latency_ms != null && <span>{(message.latency_ms / 1000).toFixed(1)} s</span>}
          </div>
        )}
      </div>
    </div>
  );
}
