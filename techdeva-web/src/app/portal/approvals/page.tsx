"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { API_BASE } from "@/lib/api";
import { useSession } from "@/lib/useTenant";

/**
 * The approval queue.
 *
 * The orchestrator stops before anything irreversible and waits for a human.
 * Until now that decision had no screen, so work parked at the gate stayed
 * parked. Approving here resumes the task server-side; rejecting closes it
 * with a recorded reason. Nothing is published or spent without a click here.
 */

interface Deliverable {
  id: string;
  task_id: string;
  agent: string;
  kind: string;
  title: string;
  body: Record<string, unknown> | null;
  status: string;
  requires_approval: boolean;
  decision_reason?: string | null;
  created_at?: string;
}

interface Payload {
  deliverables: Deliverable[];
  awaiting_approval: number;
}

function Work({ body }: { body: Record<string, unknown> | null }) {
  if (!body) return null;
  const work = (body.work ?? body) as unknown;
  const text = typeof work === "string" ? work : JSON.stringify(work, null, 2);
  if (!text || text === "{}") {
    return (
      <p className="mt-3 text-xs text-[var(--muted)]">
        The agent recorded no detail for this item.
      </p>
    );
  }
  return (
    <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-white/10 bg-black/30 p-3 text-xs text-white/75">
      {text}
    </pre>
  );
}

export default function ApprovalsPage() {
  const { session, loading } = useSession();
  const [data, setData] = useState<Payload | null | undefined>(undefined);
  const [busy, setBusy] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetch(`${API_BASE}/portal/deliverables`, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => setData(null));
  }, []);

  useEffect(() => {
    if (session) load();
  }, [session, load]);

  async function decide(id: string, action: "approve" | "reject", why?: string) {
    setBusy(id);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/portal/deliverables/${id}/${action}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: action === "reject" ? JSON.stringify({ reason: why }) : undefined,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        setError(detail?.detail ?? `Could not ${action} this item.`);
      } else {
        setRejecting(null);
        setReason("");
        load();
      }
    } catch {
      setError("Network error — nothing was changed.");
    }
    setBusy(null);
  }

  if (loading) return <p className="text-sm text-[var(--muted)]">Loading…</p>;

  if (!session) {
    return (
      <p className="text-sm text-[var(--muted)]">
        You are signed out.{" "}
        <Link className="underline" href="/login">
          Sign in
        </Link>{" "}
        to review what is waiting.
      </p>
    );
  }

  if (data === undefined) return <p className="text-sm text-[var(--muted)]">Loading approvals…</p>;
  if (data === null) return <p className="text-sm text-red-400">Could not load your approvals.</p>;

  const pending = data.deliverables.filter((d) => d.status === "awaiting_approval");
  const decided = data.deliverables.filter((d) => d.status !== "awaiting_approval");

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-semibold text-white">Approvals</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">
        Your AI team stops here before anything goes out or costs money. Nothing below has
        happened yet.
      </p>

      {error && <p className="mt-4 text-sm text-red-400">{error}</p>}

      {pending.length === 0 ? (
        <p className="mt-8 text-sm text-[var(--muted)]">
          Nothing is waiting on you right now.
        </p>
      ) : (
        <ul className="mt-8 space-y-4">
          {pending.map((d) => (
            <li key={d.id} className="rounded-lg border border-white/10 bg-white/[0.02] p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-medium text-white">{d.title}</p>
                  <p className="mt-1 text-xs uppercase tracking-wide text-[var(--muted)]">
                    {d.agent} · {d.kind}
                  </p>
                </div>
              </div>

              <Work body={d.body} />

              {rejecting === d.id ? (
                <div className="mt-4">
                  <label className="block text-xs text-[var(--muted)]">
                    Why are you rejecting this? Your AI team learns from the reason.
                    <input
                      autoFocus
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      className="mt-1 w-full rounded border border-white/10 bg-black/30 px-3 py-2 text-sm text-white focus:border-white/30 focus:outline-none"
                    />
                  </label>
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      disabled={busy === d.id || !reason.trim()}
                      onClick={() => void decide(d.id, "reject", reason.trim())}
                      className="rounded bg-red-500/80 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
                    >
                      Confirm reject
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setRejecting(null);
                        setReason("");
                      }}
                      className="rounded border border-white/15 px-4 py-2 text-sm text-white/80"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="mt-4 flex gap-2">
                  <button
                    type="button"
                    disabled={busy === d.id}
                    onClick={() => void decide(d.id, "approve")}
                    className="rounded bg-white/90 px-4 py-2 text-sm font-medium text-black disabled:opacity-40"
                  >
                    {busy === d.id ? "Working…" : "Approve"}
                  </button>
                  <button
                    type="button"
                    disabled={busy === d.id}
                    onClick={() => setRejecting(d.id)}
                    className="rounded border border-white/15 px-4 py-2 text-sm text-white/80"
                  >
                    Reject
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {decided.length > 0 && (
        <section className="mt-10">
          <h2 className="text-sm font-medium text-white">Already decided</h2>
          <ul className="mt-3 space-y-1 text-sm text-[var(--muted)]">
            {decided.slice(0, 20).map((d) => (
              <li key={d.id}>
                {d.title} — {d.status}
                {d.decision_reason ? ` (${d.decision_reason})` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
