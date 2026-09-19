"use client";

import { useEffect, useState } from "react";
import { StatCard } from "@/components/StatCard";
import { useTenant } from "@/lib/useTenant";
import { getAttributionReport, getLeads, type AttributionReport, type Lead } from "@/lib/api";

export default function PortalOverview() {
  const tenantId = useTenant();
  const [report, setReport] = useState<AttributionReport | null>(null);
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    getAttributionReport(tenantId).then((r) => (r.ok ? setReport(r.data) : setError(r.error)));
    getLeads(tenantId).then((r) => (r.ok ? setLeads(r.data) : setError(r.error)));
  }, [tenantId]);

  if (!tenantId) {
    return (
      <p className="text-sm text-[var(--muted)]">
        No tenant selected — open this portal with <code>?tenant=&lt;your-tenant-id&gt;</code> once,
        or sign in once the login flow is wired to the platform&apos;s real auth.
      </p>
    );
  }

  return (
    <div>
      <h1 className="text-xl font-semibold text-white">Overview</h1>
      {error ? <p className="mt-2 text-sm text-[var(--danger)]">Could not load live data: {error}</p> : null}
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Open leads" value={leads ? leads.filter((l) => l.status !== "lost").length : null} />
        <StatCard label="ROAS" value={report?.roas ?? null} suffix="×" />
        <StatCard label="CPL" value={report?.cpl ?? null} />
        <StatCard label="Spend" value={report?.spend ?? null} />
      </div>
    </div>
  );
}
