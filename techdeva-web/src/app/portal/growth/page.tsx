"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { API_BASE } from "@/lib/api";
import { useSession } from "@/lib/useTenant";

/**
 * The client's own research and growth plan.
 *
 * AnalysisAgent and GrowthPlannerAgent already run and store their output;
 * this page reads it back. It renders what exists and names what does not,
 * rather than filling the screen with a plausible-looking plan.
 */

interface Stage {
  type: string;
  status: string;
  count: number;
  last_at: string | null;
}

interface Growth {
  analysis: unknown;
  content_plan: unknown;
  pipeline: Stage[];
  has_analysis: boolean;
  has_plan: boolean;
  missing: string[];
}

const STATUS_LABEL: Record<string, string> = {
  done: "Done",
  waiting: "Waiting",
  needs_human: "Needs your approval",
  failed: "Failed",
};

function asText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export default function GrowthPage() {
  const { session, loading } = useSession();
  const [data, setData] = useState<Growth | null | undefined>(undefined);

  useEffect(() => {
    if (!session) return;
    fetch(`${API_BASE}/portal/growth`, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => setData(null));
  }, [session]);

  if (loading) return <p className="text-sm text-[var(--muted)]">Loading…</p>;

  if (!session) {
    return (
      <p className="text-sm text-[var(--muted)]">
        You are signed out.{" "}
        <Link className="underline" href="/login">
          Sign in
        </Link>{" "}
        to see your growth plan.
      </p>
    );
  }

  if (data === undefined) return <p className="text-sm text-[var(--muted)]">Reading your plan…</p>;

  if (data === null) {
    return <p className="text-sm text-red-400">Could not load your growth plan.</p>;
  }

  const needsApproval = data.pipeline
    .filter((s) => s.status === "needs_human")
    .reduce((n, s) => n + s.count, 0);

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-semibold text-white">Growth plan</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">
        What your AI team has researched about your market, and what it plans to do about it.
      </p>

      {needsApproval > 0 && (
        <p className="mt-4 rounded border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          {needsApproval} item{needsApproval === 1 ? "" : "s"} waiting for your approval before
          anything is published or spent.
        </p>
      )}

      <section className="mt-8">
        <h2 className="text-sm font-medium text-white">Market research</h2>
        {data.has_analysis ? (
          <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded border border-white/10 bg-black/30 p-4 text-xs text-white/80">
            {asText(data.analysis)}
          </pre>
        ) : (
          <p className="mt-3 text-sm text-[var(--muted)]">
            NOT RUN YET. Research starts once your business details and competitors are in{" "}
            <Link className="underline" href="/portal/onboarding">
              Set up business
            </Link>
            .
          </p>
        )}
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-medium text-white">Plan</h2>
        {data.has_plan ? (
          <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded border border-white/10 bg-black/30 p-4 text-xs text-white/80">
            {asText(data.content_plan)}
          </pre>
        ) : (
          <p className="mt-3 text-sm text-[var(--muted)]">
            NOT GENERATED YET. The plan is built from the research above.
          </p>
        )}
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-medium text-white">What your AI team is doing</h2>
        {data.pipeline.length === 0 ? (
          <p className="mt-3 text-sm text-[var(--muted)]">No work queued yet.</p>
        ) : (
          <table className="mt-3 w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-[var(--muted)]">
              <tr>
                <th className="py-2">Work</th>
                <th className="py-2">State</th>
                <th className="py-2 text-right">Count</th>
              </tr>
            </thead>
            <tbody className="text-white/80">
              {data.pipeline.map((s) => (
                <tr key={`${s.type}-${s.status}`} className="border-t border-white/5">
                  <td className="py-2">{s.type.replace(/_/g, " ")}</td>
                  <td className="py-2">{STATUS_LABEL[s.status] ?? s.status}</td>
                  <td className="py-2 text-right tabular-nums">{s.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
