"use client";

import { useEffect, useState } from "react";
import { useTenant } from "@/lib/useTenant";
import { getIntegrations, API_BASE, type Integration } from "@/lib/api";

export default function IntegrationsPage() {
  const tenantId = useTenant();
  const [integrations, setIntegrations] = useState<Integration[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    getIntegrations(tenantId).then((r) => (r.ok ? setIntegrations(r.data) : setError(r.error)));
  }, [tenantId]);

  return (
    <div>
      <h1 className="text-xl font-semibold text-white">Integrations</h1>
      {error ? <p className="mt-4 text-sm text-[var(--danger)]">Could not load: {error}</p> : null}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {(integrations ?? [{ provider: "meta", connected: false }, { provider: "google", connected: false }]).map(
          (i) => (
            <div key={i.provider} className="td-card flex items-center justify-between p-4">
              <span className="font-medium capitalize text-white">{i.provider}</span>
              {i.connected ? (
                <span className="text-sm text-[var(--success)]">Connected</span>
              ) : (
                <a
                  href={`${API_BASE}/connect/${i.provider}?tenant=${tenantId ?? ""}`}
                  className="td-btn-primary px-3 py-1.5 text-sm"
                >
                  Connect
                </a>
              )}
            </div>
          ),
        )}
      </div>
    </div>
  );
}
