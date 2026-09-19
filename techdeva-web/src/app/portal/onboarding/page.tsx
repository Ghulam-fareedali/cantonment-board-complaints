"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { createResource, listResource, Row } from "@/lib/business";
import { useSession } from "@/lib/useTenant";

/**
 * Guided onboarding, inside the portal and driven by the session.
 *
 * Each step writes to the same /business/{resource} API the agents already
 * read from, so finishing a step changes the product rather than a form's
 * local state. Steps whose backing integration is not proven end to end are
 * shown as NOT CONNECTED and link to Integrations instead of pretending.
 */

interface Field {
  name: string;
  label: string;
  placeholder?: string;
  required?: boolean;
}

interface Step {
  key: string;
  title: string;
  blurb: string;
  resource: string;
  fields: Field[];
  summary: (row: Row) => string;
}

const STEPS: Step[] = [
  {
    key: "location",
    title: "Where you operate",
    blurb: "Your address and timezone. The AI agent quotes travel, hours and directions from this.",
    resource: "locations",
    fields: [
      { name: "name", label: "Branch name", placeholder: "Main branch", required: true },
      { name: "address", label: "Address", placeholder: "Street address" },
      { name: "city", label: "City", placeholder: "Lahore" },
      { name: "country", label: "Country", placeholder: "Pakistan" },
      { name: "phone", label: "Phone", placeholder: "+92 300 0000000" },
      { name: "timezone", label: "Timezone", placeholder: "Asia/Karachi" },
    ],
    summary: (r) => [r.name, r.city].filter(Boolean).join(" — "),
  },
  {
    key: "services",
    title: "What you sell",
    blurb: "Every service with its price. The agent will not quote a price that is not on this list.",
    resource: "services",
    fields: [
      { name: "name", label: "Service", placeholder: "Hair PRP", required: true },
      { name: "category", label: "Category", placeholder: "Treatments" },
      { name: "price", label: "Price", placeholder: "15000" },
      { name: "currency", label: "Currency", placeholder: "PKR" },
      { name: "duration_minutes", label: "Duration (minutes)", placeholder: "60" },
    ],
    summary: (r) => [r.name, r.price].filter(Boolean).join(" — "),
  },
  {
    key: "hours",
    title: "When you are open",
    blurb: "Opening hours per day. Bookings outside these hours are refused rather than promised.",
    resource: "business_hours",
    fields: [
      { name: "day_of_week", label: "Day (0=Mon … 6=Sun)", placeholder: "0", required: true },
      { name: "opens", label: "Opens", placeholder: "11:00" },
      { name: "closes", label: "Closes", placeholder: "20:00" },
    ],
    summary: (r) => `Day ${r.day_of_week}: ${r.opens ?? "?"}–${r.closes ?? "?"}`,
  },
  {
    key: "policies",
    title: "Your rules",
    blurb: "Deposits, cancellations, refunds. The agent answers from these instead of improvising.",
    resource: "business_policies",
    fields: [
      { name: "kind", label: "Kind", placeholder: "cancellation", required: true },
      { name: "title", label: "Title", placeholder: "24-hour cancellation", required: true },
      { name: "body", label: "Policy", placeholder: "Describe the policy", required: true },
    ],
    summary: (r) => String(r.title ?? r.kind ?? ""),
  },
];

const NUMERIC = new Set(["price", "duration_minutes", "day_of_week"]);

export default function OnboardingPage() {
  const { session, loading } = useSession();
  const [rows, setRows] = useState<Record<string, Row[]>>({});
  const [open, setOpen] = useState<string>(STEPS[0].key);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const entries = await Promise.all(
      STEPS.map(async (s) => [s.key, (await listResource(s.resource)) ?? []] as const),
    );
    setRows(Object.fromEntries(entries));
  }, []);

  useEffect(() => {
    if (session) void refresh();
  }, [session, refresh]);

  if (loading) return <p className="text-sm text-[var(--muted)]">Loading your workspace…</p>;

  if (!session) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-white">Onboarding</h1>
        <p className="mt-4 text-sm text-[var(--muted)]">
          You are signed out.{" "}
          <Link className="underline" href="/login">
            Sign in
          </Link>{" "}
          to set up your business.
        </p>
      </div>
    );
  }

  const done = STEPS.filter((s) => (rows[s.key]?.length ?? 0) > 0).length;

  async function save(step: Step) {
    setBusy(true);
    setError(null);
    const body: Row = {};
    for (const f of step.fields) {
      const raw = (draft[f.name] ?? "").trim();
      if (!raw) continue;
      body[f.name] = NUMERIC.has(f.name) ? Number(raw) : raw;
    }
    const missing = step.fields.filter((f) => f.required && !body[f.name]);
    if (missing.length) {
      setError(`${missing.map((f) => f.label).join(", ")} required`);
      setBusy(false);
      return;
    }
    const created = await createResource(step.resource, body);
    if (!created) setError("Could not save. Your session may have expired.");
    else {
      setDraft({});
      await refresh();
    }
    setBusy(false);
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-semibold text-white">Set up your business</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">
        {done} of {STEPS.length} complete. Your AI agent answers customers from what you enter
        here, so anything left blank is something it will not know.
      </p>

      <div className="mt-4 h-1 w-full rounded bg-white/10">
        <div
          className="h-1 rounded bg-white/70 transition-all"
          style={{ width: `${(done / STEPS.length) * 100}%` }}
        />
      </div>

      <div className="mt-8 space-y-4">
        {STEPS.map((step) => {
          const saved = rows[step.key] ?? [];
          const isOpen = open === step.key;
          return (
            <section key={step.key} className="rounded-lg border border-white/10 bg-white/[0.02]">
              <button
                type="button"
                onClick={() => setOpen(isOpen ? "" : step.key)}
                className="flex w-full items-center justify-between px-5 py-4 text-left"
              >
                <span>
                  <span className="text-sm font-medium text-white">{step.title}</span>
                  <span className="ml-3 text-xs uppercase tracking-wide text-[var(--muted)]">
                    {saved.length ? `${saved.length} saved` : "Action required"}
                  </span>
                </span>
                <span className="text-[var(--muted)]">{isOpen ? "−" : "+"}</span>
              </button>

              {isOpen && (
                <div className="border-t border-white/10 px-5 py-5">
                  <p className="text-sm text-[var(--muted)]">{step.blurb}</p>

                  {saved.length > 0 && (
                    <ul className="mt-4 space-y-1 text-sm text-white/80">
                      {saved.map((r, i) => (
                        <li key={String(r.id ?? i)}>• {step.summary(r)}</li>
                      ))}
                    </ul>
                  )}

                  <div className="mt-5 grid gap-3 sm:grid-cols-2">
                    {step.fields.map((f) => (
                      <label key={f.name} className="block text-xs text-[var(--muted)]">
                        {f.label}
                        {f.required && <span className="text-white/60"> *</span>}
                        <input
                          value={draft[f.name] ?? ""}
                          placeholder={f.placeholder}
                          onChange={(e) => setDraft({ ...draft, [f.name]: e.target.value })}
                          className="mt-1 w-full rounded border border-white/10 bg-black/30 px-3 py-2 text-sm text-white placeholder:text-white/25 focus:border-white/30 focus:outline-none"
                        />
                      </label>
                    ))}
                  </div>

                  {error && <p className="mt-3 text-sm text-red-400">{error}</p>}

                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void save(step)}
                    className="mt-5 rounded bg-white/90 px-4 py-2 text-sm font-medium text-black disabled:opacity-50"
                  >
                    {busy ? "Saving…" : `Add ${step.title.toLowerCase()}`}
                  </button>
                </div>
              )}
            </section>
          );
        })}

        <section className="rounded-lg border border-dashed border-white/10 px-5 py-4">
          <p className="text-sm font-medium text-white">WhatsApp and Meta</p>
          <p className="mt-1 text-sm text-[var(--muted)]">
            NOT CONNECTED. Self-service connection is not built yet — these are set up for you for
            now. Progress is shown on{" "}
            <Link className="underline" href="/portal/integrations">
              Integrations
            </Link>
            .
          </p>
        </section>
      </div>
    </div>
  );
}
