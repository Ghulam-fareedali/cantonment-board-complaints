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
      <h1 className="text-xl font-semibold text-white">Leads / CRM</h1>
      {!tenantId ? (
        <p className="mt-4 text-sm text-[var(--muted)]">No tenant selected.</p>
      ) : error ? (
        <p className="mt-4 text-sm text-[var(--danger)]">Could not load leads: {error}</p>
      ) : !leads ? (
        <p className="mt-4 text-sm text-[var(--muted)]">Loading…</p>
      ) : leads.length === 0 ? (
        <p className="mt-4 text-sm text-[var(--muted)]">No leads yet.</p>
      ) : (
        <div className="mt-4 overflow-x-auto td-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-white/10 bg-white/[0.02] text-[var(--muted)]">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Phone</th>
                <th className="px-4 py-2 font-medium">Service</th>
                <th className="px-4 py-2 font-medium">Source</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {leads.map((l) => (
                <tr key={l.id} className="text-white">
                  <td className="px-4 py-2">{l.name ?? "—"}</td>
                  <td className="px-4 py-2">{l.phone ?? "—"}</td>
                  <td className="px-4 py-2">{l.service ?? "—"}</td>
                  <td className="px-4 py-2 text-[var(--muted)]">{l.source}</td>
                  <td className="px-4 py-2 text-[var(--muted)]">{l.status}</td>
                  <td className="px-4 py-2 text-[var(--muted)]">{new Date(l.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
