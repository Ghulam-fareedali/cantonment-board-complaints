"use client";

import { useEffect, useState } from "react";
import { useTenant } from "@/lib/useTenant";
import { getLeads, type Lead } from "@/lib/api";

export default function LeadsPage() {
  const tenantId = useTenant();
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    getLeads(tenantId).then((r) => (r.ok ? setLeads(r.data) : setError(r.error)));
  }, [tenantId]);

  return (
    <div>
      <h1 className="text-xl font-semibold text-neutral-900 dark:text-white">Leads / CRM</h1>
      {!tenantId ? (
        <p className="mt-4 text-sm text-neutral-600 dark:text-neutral-300">No tenant selected.</p>
      ) : error ? (
        <p className="mt-4 text-sm text-red-600">Could not load leads: {error}</p>
      ) : !leads ? (
        <p className="mt-4 text-sm text-neutral-500">Loading…</p>
      ) : leads.length === 0 ? (
        <p className="mt-4 text-sm text-neutral-500">No leads yet.</p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-xl border border-black/10 dark:border-white/10">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-black/10 bg-neutral-50 text-neutral-500 dark:border-white/10 dark:bg-neutral-900">
              <tr>
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Phone</th>
                <th className="px-4 py-2">Service</th>
                <th className="px-4 py-2">Source</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/5 dark:divide-white/5">
              {leads.map((l) => (
                <tr key={l.id}>
                  <td className="px-4 py-2">{l.name ?? "—"}</td>
                  <td className="px-4 py-2">{l.phone ?? "—"}</td>
                  <td className="px-4 py-2">{l.service ?? "—"}</td>
                  <td className="px-4 py-2">{l.source}</td>
                  <td className="px-4 py-2">{l.status}</td>
                  <td className="px-4 py-2">{new Date(l.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
