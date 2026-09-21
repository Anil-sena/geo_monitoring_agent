export type RiskLevel = "NONE" | "LOW" | "MEDIUM" | "HIGH";
export type RunStatus =
  | "queued"
  | "fetching_imagery"
  | "detecting_change"
  | "assessing_risk"
  | "completed"
  | "failed";

export interface Bbox {
  minx: number;
  miny: number;
  maxx: number;
  maxy: number;
}

export interface RunParams extends Bbox {
  days_back_t1: number;
  days_back_t2: number;
  window_days: number;
  threshold: number;
  buffer_m: number;
  sensor?: "S1" | "S2";
  method?: string;
  label?: string | null;
}

export interface Polygon {
  id: number;
  area_m2: number | null;
  centroid_lat: number | null;
  centroid_lon: number | null;
  risk_score: number;
  near_infra: boolean;
  distance_to_infra_m: number | null;
  nearest_infra_type: string | null;
  nearest_infra_name: string | null;
}

export interface Run extends RunParams {
  id: string;
  run_key: string;
  status: RunStatus;
  requested_by: string | null;
  t1_window: string | null;
  t2_window: string | null;
  t1_scene_count: number | null;
  t2_scene_count: number | null;
  t1_latest_date: string | null;
  t2_latest_date: string | null;
  is_mock: boolean;
  changed_percent: number | null;
  n_change_polygons: number | null;
  n_near_infra: number | null;
  max_risk_score: number | null;
  risk_level: RiskLevel | null;
  infra_feature_count: number | null;
  narrative: string | null;
  error: string | null;
  timings: Record<string, number> | null;
  duration_s: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  previews: Partial<Record<"t1" | "t2" | "change", string>>;
  polygons: Polygon[];
}

export interface Preset extends Bbox {
  id: string;
  name: string;
  description: string | null;
}

export interface Health {
  status: string;
  env: string;
  earth_engine: string;
  llm_provider: string | null;
  llm_configured: boolean;
  database: string;
  runs_total: number;
}

export interface ChatAnswer {
  session_id: string;
  message_id: number;
  answer: string;
  run_ids: string[];
  latency_ms: number;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  run_id: string | null;
  latency_ms: number | null;
  created_at: string;
}

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health"),
  presets: () => request<Preset[]>("/presets"),
  runs: (limit = 20, offset = 0) =>
    request<{ items: Run[]; total: number }>(`/runs?limit=${limit}&offset=${offset}`),
  run: (id: string) => request<Run>(`/runs/${id}`),
  createRun: (params: RunParams) =>
    request<Run>("/runs", { method: "POST", body: JSON.stringify(params) }),
  deleteRun: (id: string) => request<void>(`/runs/${id}`, { method: "DELETE" }),
  polygons: (id: string) => request<GeoJSON.FeatureCollection>(`/runs/${id}/polygons`),
  infrastructure: (id: string) => request<GeoJSON.FeatureCollection>(`/runs/${id}/infrastructure`),
  chat: (body: { message: string; session_id?: string; aoi?: RunParams }) =>
    request<ChatAnswer>("/chat", { method: "POST", body: JSON.stringify(body) }),
  chatHistory: (sessionId: string) => request<ChatMessage[]>(`/chat/${sessionId}`),
  eventsUrl: (id: string) => `${BASE}/runs/${id}/events`,
  downloadUrl: (id: string, name: string) => `${BASE}/runs/${id}/download/${name}`,
};

export const STAGES: { key: RunStatus; label: string }[] = [
  { key: "queued", label: "Queued" },
  { key: "fetching_imagery", label: "Fetching Sentinel-2 composites" },
  { key: "detecting_change", label: "Detecting change" },
  { key: "assessing_risk", label: "Scoring infrastructure proximity" },
  { key: "completed", label: "Complete" },
];

export function stageIndex(status: RunStatus): number {
  const i = STAGES.findIndex((s) => s.key === status);
  return i === -1 ? STAGES.length - 1 : i;
}

export const RISK_COLOUR: Record<RiskLevel, string> = {
  HIGH: "#e4573d",
  MEDIUM: "#f2a93b",
  LOW: "#e9d46a",
  NONE: "#6faf8e",
};

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function fmtHa(m2: number | null | undefined): string {
  if (m2 == null) return "—";
  return m2 >= 10000 ? `${(m2 / 10000).toFixed(2)} ha` : `${Math.round(m2)} m²`;
}
