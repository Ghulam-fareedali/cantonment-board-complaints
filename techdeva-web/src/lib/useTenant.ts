"use client";

import { useEffect, useState } from "react";

import { API_BASE } from "./api";

/**
 * The tenant the signed-in session belongs to.
 *
 * The browser does not get to choose this. Earlier builds read the tenant id
 * from `?tenant=<id>` or localStorage because no session endpoint had been
 * confirmed; the API now decides identity in `resolve_scope()` and ignores
 * client-supplied scope entirely, so the only honest source is `/me`, read
 * with the session cookie.
 *
 * Returns `undefined` while the session is still loading and `null` when
 * there is no session, so a caller can tell "not yet" from "not signed in".
 */
export function useTenant(): string | null | undefined {
  const { session } = useSession();
  if (session === undefined) return undefined;
  return session?.tenant_id ?? null;
}

export interface Session {
  tenant_id: string | null;
  role: string | null;
  user_id: string | null;
  authenticated: boolean;
  auth_enforced: boolean;
}

export function useSession() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/me`, { credentials: "include", cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: Session | null) => {
        if (cancelled) return;
        setSession(data && data.authenticated ? data : null);
      })
      .catch(() => {
        if (!cancelled) setSession(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { session, loading: session === undefined };
}
