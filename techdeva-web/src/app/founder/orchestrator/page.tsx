"use client";

import { useEffect, useState } from "react";
import { StatCard } from "@/components/StatCard";
import { getOrchestratorStatus, type OrchestratorStatus } from "@/lib/api";

export default function OrchestratorPage() {
  const [status, setStatus] = useState<OrchestratorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getOrchestratorStatus().then((r) => (r.ok ? setStatus(r.data) : setError(r.error)));
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold text-neutral-900 dark:text-white">Orchestrator</h1>
      {error ? <p className="mt-2 text-sm text-red-600">Could not load: {error}</p> : null}
      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <StatCard label="Events queue" value={status?.queue?.events ?? null} />
        <StatCard label="Tasks queue" value={status?.queue?.tasks ?? null} />
      </div>
    </div>
  );
}
