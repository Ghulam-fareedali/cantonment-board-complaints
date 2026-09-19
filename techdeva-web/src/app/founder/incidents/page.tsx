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
      <h1 className="text-xl font-semibold text-neutral-900 dark:text-white">Incidents</h1>
      {error ? <p className="mt-4 text-sm text-red-600">Could not load: {error}</p> : null}
      {!incidents ? null : incidents.length === 0 ? (
        <p className="mt-4 text-sm text-neutral-500">No incidents recorded.</p>
      ) : (
        <div className="mt-4 divide-y divide-black/5 dark:divide-white/5">
          {incidents.map((i) => (
            <div key={i.id} className="flex items-center justify-between py-3 text-sm">
              <span>{i.type}</span>
              <span className="text-neutral-500">{i.severity}</span>
              <span className={i.resolved ? "text-green-600" : "text-red-600"}>
                {i.resolved ? "resolved" : "open"}
              </span>
              <span className="text-neutral-500">{new Date(i.created_at).toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
