"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Health } from "@/lib/api";

const NAV = [
  { href: "/", label: "Console" },
  { href: "/runs", label: "History" },
];

export function TopBar() {
  const path = usePathname();
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .health()
        .then((h) => alive && (setHealth(h), setDown(false)))
        .catch(() => alive && setDown(true));
    tick();
    const t = setInterval(tick, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <header className="h-14 shrink-0 flex items-center gap-6 px-5 border-b border-white/5 bg-ink-900/80 backdrop-blur z-[1000]">
      <Link href="/" className="flex items-center gap-2.5">
        <span className="relative inline-flex w-6 h-6">
          <span className="absolute inset-0 rounded-full border-2 border-teal/70" />
          <span className="absolute inset-[7px] rounded-full bg-amber" />
        </span>
        <span className="font-semibold tracking-tight">GeoAgent Monitor</span>
      </Link>

      <nav className="flex items-center gap-1 text-sm">
        {NAV.map((n) => {
          const active = n.href === "/" ? path === "/" || path === "" : path.startsWith(n.href);
          return (
            <Link
              key={n.href}
              href={n.href}
              className={`px-3 py-1.5 rounded-md transition ${
                active ? "bg-white/10 text-fog-100" : "text-fog-500 hover:text-fog-100 hover:bg-white/5"
              }`}
            >
              {n.label}
            </Link>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-4 text-xs text-fog-500">
        <Status ok={!down && !!health} label={down ? "API offline" : "API"} />
        <Status
          ok={health?.earth_engine === "ready" || health?.earth_engine === "configured"}
          label={`Earth Engine ${health?.earth_engine ?? "…"}`}
        />
        <Status ok={!!health?.llm_configured} label={health?.llm_configured ? `Agent · ${health.llm_provider}` : "Agent off"} />
      </div>
    </header>
  );
}

function Status({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? "bg-risk-none" : "bg-fog-700"}`} />
      {label}
    </span>
  );
}
