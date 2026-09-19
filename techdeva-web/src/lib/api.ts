// Thin client for the TechDeva AIBOS API.
//
// Honesty rule (see Constitution invariant #2): a metric with no data is
// `null`, never a fabricated 0. Every helper here returns `null` on any
// failure (network, non-2xx, bad shape) instead of throwing a fake number
// into a dashboard. Callers render "—" for null, never "0".

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "https://api.techdeva.net";

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: string };

async function apiFetch<T>(
  path: string,
  opts: { tenantId?: string; token?: string; init?: RequestInit } = {},
): Promise<ApiResult<T>> {
  const { tenantId, token, init } = opts;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (tenantId) headers["X-Tenant-Id"] = tenantId;
  if (token) headers["Authorization"] = `Bearer ${token}`;

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      // Server-rendered pages should always hit the live API, not a stale cache.
      cache: "no-store",
    });
    if (!res.ok) {
      return { ok: false, error: `${res.status} ${res.statusText}` };
    }
    const data = (await res.json()) as T;
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "network error" };
  }
}

// ---- Founder / cross-tenant (see app/main.py: /techdeva/overview, /orchestrator/status, /incidents, /ai/health, /tasks) ----

export interface TechDevaOverview {
  tenants?: number;
  active_agents?: number;
  revenue_mtd?: number | null;
  [key: string]: unknown;
}
export const getTechDevaOverview = (token?: string) =>
  apiFetch<TechDevaOverview>("/techdeva/overview", { token });

export interface OrchestratorStatus {
  queue?: { events?: number; tasks?: number };
  [key: string]: unknown;
}
export const getOrchestratorStatus = (token?: string) =>
  apiFetch<OrchestratorStatus>("/orchestrator/status", { token });

export interface Incident {
  id: string;
  type: string;
  severity: string;
  created_at: string;
  resolved: boolean;
  [key: string]: unknown;
}
export const getIncidents = (token?: string) =>
  apiFetch<Incident[]>("/incidents", { token });

export interface AiHealth {
  provider?: string;
  status?: string;
  [key: string]: unknown;
}
export const getAiHealth = (token?: string) => apiFetch<AiHealth>("/ai/health", { token });

// ---- Tenant-scoped (see app/main.py: /leads, /revenue report, /attribution/report, /report, /integrations) ----

export interface Lead {
  id: string;
  name?: string;
  phone?: string;
  status: string;
  source: string;
  service?: string;
  created_at: string;
  [key: string]: unknown;
}
export const getLeads = (tenantId: string, token?: string) =>
  apiFetch<Lead[]>("/leads", { tenantId, token });

export interface AttributionReport {
  roas?: number | null;
  cpl?: number | null;
  spend?: number | null;
  revenue?: number | null;
  [key: string]: unknown;
}
export const getAttributionReport = (tenantId: string, token?: string) =>
  apiFetch<AttributionReport>("/attribution/report", { tenantId, token });

export interface TenantReport {
  [key: string]: unknown;
}
export const getReport = (tenantId: string, token?: string) =>
  apiFetch<TenantReport>("/report", { tenantId, token });

export interface Integration {
  provider: string;
  connected: boolean;
  status?: string;
  [key: string]: unknown;
}
export const getIntegrations = (tenantId: string, token?: string) =>
  apiFetch<Integration[]>("/integrations", { tenantId, token });
