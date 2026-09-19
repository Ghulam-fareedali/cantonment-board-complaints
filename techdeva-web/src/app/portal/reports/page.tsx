"use client";

import { useEffect, useState } from "react";
import { StatCard } from "@/components/StatCard";
import { useTenant } from "@/lib/useTenant";
import { getAttributionReport, type AttributionReport } from "@/lib/api";

export default function ReportsPage() {
  const tenantId = useTenant();
  const [report, setReport] = useState<AttributionReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    getAttributionReport(tenantId).then((r) => (r.ok ? setReport(r.data) : setError(r.error)));
  }, [tenantId]);

  return (
    <div>
      <h1 className="text-xl font-semibold text-neutral-900 dark:text-white">Reports</h1>
      {error ? <p className="mt-2 text-sm text-red-600">Could not load: {error}</p> : null}
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Revenue" value={report?.revenue ?? null} />
        <StatCard label="Spend" value={report?.spend ?? null} />
        <StatCard label="ROAS" value={report?.roas ?? null} suffix="×" hint="Null means no spend recorded — never shown as 0." />
        <StatCard label="CPL" value={report?.cpl ?? null} />
      </div>
    </div>
  );
}
