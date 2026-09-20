"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { API_BASE } from "@/lib/api";
import { useSession } from "@/lib/useTenant";

/**
 * What this business can actually prove about each integration.
 *
 * The API computes `connected` from evidence — a stored credential and a real
 * connection moment — rather than from a status column, after an audit found
 * four rows claiming connected while holding no credential. This page shows
 * that evidence instead of collapsing everything to a green tick: a token that
 * has expired, a connection that errored, and one that was never made are
 * three different situations and the client is told which one they are in.
 */

interface Integration {
  provider: string;
  status: string;
  connected: boolean;
  health: string;
  external_account: string | null;
  connected_at: string | null;
  expires_at: string | null;
  last_checked_at: string | null;
  scopes: string[];
  last_error: string | null;
}

const LABEL: Record<string, string> = {
  meta: "Facebook & Instagram ads",
  instagram: "Instagram",
  google: "Google",
  whatsapp: "WhatsApp",
};

const BLURB: Record<string, string> = {
  meta: "Lets your AI team read ad performance and build campaigns for approval.",
  instagram: "Comes with Facebook — the same connection covers both.",
  google: "Search ads and your Business Profile.",
  whatsapp: "The number your AI sales agent answers on.",
};

/** Providers that cannot be self-connected yet — say so rather than show a dead button. */
const NOT_SELF_SERVICE = new Set(["whatsapp", "instagram"]);

function expired(i: Integration): boolean {
  return !!i.expires_at && new Date(i.expires_at).getTime() < Date.now();
}

function State({ i }: { i: Integration }) {
  if (i.last_error) {
    return <span className="text-sm text-red-400">Problem — {i.last_error}</span>;
  }
  if (i.connected && expired(i)) {
    return <span className="text-sm text-amber-300">Expired — needs reconnecting</span>;
  }
  if (i.connected) {
    return (
      <span className="text-sm text-emerald-400">
        Connected{i.external_account ? ` · ${i.external_account}` : ""}
      </span>
    );
  }
  return <span className="text-sm text-[var(--muted)]">Not connected</span>;
}

export default function IntegrationsPage() {
  const { session, loading } = useSession();
  const [items, setItems] = useState<Integration[] | null | undefined>(undefined);

  const load = useCallback(() => {
    fetch(`${API_BASE}/integrations`, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setItems(d?.integrations ?? null))
      .catch(() => setItems(null));
  }, []);

  useEffect(() => {
    if (session) load();
  }, [session, load]);

  if (loading) return <p className="text-sm text-[var(--muted)]">Loading…</p>;

  if (!session) {
    return (
      <p className="text-sm text-[var(--muted)]">
        You are signed out.{" "}
        <Link className="underline" href="/login">
          Sign in
        </Link>{" "}
        to manage your connections.
      </p>
    );
  }

  if (items === undefined) return <p className="text-sm text-[var(--muted)]">Loading connections…</p>;
  if (items === null) return <p className="text-sm text-red-400">Could not load your connections.</p>;

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-semibold text-white">Connections</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">
        Your AI team can only work with what is connected here. A connection is shown as live
        only when a working credential is actually stored.
      </p>

      <div className="mt-8 space-y-3">
        {items.map((i) => {
          const needsAction = !i.connected || expired(i) || !!i.last_error;
          const selfService = !NOT_SELF_SERVICE.has(i.provider);
          return (
            <section
              key={i.provider}
              className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-white/10 bg-white/[0.02] px-5 py-4"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-white">
                  {LABEL[i.provider] ?? i.provider}
                </p>
                <p className="mt-1 text-xs text-[var(--muted)]">{BLURB[i.provider] ?? ""}</p>
                <p className="mt-2">
                  <State i={i} />
                </p>
                {i.scopes.length > 0 && (
                  <p className="mt-1 text-xs text-[var(--muted)]">
                    Permissions: {i.scopes.join(", ")}
                  </p>
                )}
              </div>

              {needsAction && selfService && (
                <a
                  href={`${API_BASE}/connect/${i.provider}`}
                  className="shrink-0 rounded bg-white/90 px-4 py-2 text-sm font-medium text-black"
                >
                  {i.connected ? "Reconnect" : "Connect"}
                </a>
              )}
              {needsAction && !selfService && (
                <span className="shrink-0 text-xs uppercase tracking-wide text-[var(--muted)]">
                  {i.provider === "instagram" ? "Via Facebook" : "Set up for you"}
                </span>
              )}
            </section>
          );
        })}
      </div>

      <p className="mt-8 text-xs text-[var(--muted)]">
        Connecting opens the provider&apos;s own permission screen. TechDeva never sees or stores
        your password.
      </p>
    </div>
  );
}
