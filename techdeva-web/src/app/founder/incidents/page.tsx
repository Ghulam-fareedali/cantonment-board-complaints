"use client";

import { useEffect, useState } from "react";
import { getIncidents, type Incident } from "@/lib/api";

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getIncidents().then((r) => (r.ok ? setIncidents(r.data) : setError(r.error)));
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold text-white">Incidents</h1>
      {error ? <p className="mt-4 text-sm text-[var(--danger)]">Could not load: {error}</p> : null}
      {!incidents ? null : incidents.length === 0 ? (
        <p className="mt-4 text-sm text-[var(--muted)]">No incidents recorded.</p>
      ) : (
        <div className="mt-4 divide-y divide-white/5">
          {incidents.map((i) => (
            <div key={i.id} className="flex items-center justify-between py-3 text-sm text-white">
              <span>{i.type}</span>
              <span className="text-[var(--muted)]">{i.severity}</span>
              <span className={i.resolved ? "text-[var(--success)]" : "text-[var(--danger)]"}>
                {i.resolved ? "resolved" : "open"}
              </span>
              <span className="text-[var(--muted)]">{new Date(i.created_at).toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
