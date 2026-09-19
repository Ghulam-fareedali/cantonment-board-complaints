"use client";

import { useEffect, useState } from "react";
import { StatCard } from "@/components/StatCard";
import { getTechDevaOverview, getOrchestratorStatus, type TechDevaOverview, type OrchestratorStatus } from "@/lib/api";

export default function FounderOverview() {
  const [overview, setOverview] = useState<TechDevaOverview | null>(null);
  const [orch, setOrch] = useState<OrchestratorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTechDevaOverview().then((r) => (r.ok ? setOverview(r.data) : setError(r.error)));
    getOrchestratorStatus().then((r) => (r.ok ? setOrch(r.data) : setError(r.error)));
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold text-neutral-900 dark:text-white">Founder Overview</h1>
      {error ? (
        <p className="mt-2 text-sm text-red-600">
          Could not load live data ({error}). This console needs a real founder-scoped
          token wired to /techdeva/overview and /orchestrator/status.
        </p>
      ) : null}
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Tenants" value={overview?.tenants ?? null} />
        <StatCard label="Active agents" value={overview?.active_agents ?? null} />
        <StatCard label="Revenue (MTD)" value={overview?.revenue_mtd ?? null} />
        <StatCard label="Queued tasks" value={orch?.queue?.tasks ?? null} />
      </div>
    </div>
  );
}
