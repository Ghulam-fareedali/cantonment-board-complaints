"use client";

import { useEffect, useState } from "react";

// Placeholder session handling: the platform's actual login/session flow
// (cookies vs. JWT vs. the existing /oauth/* callbacks) was not confirmed
// against app/main.py during this build — there's no plain-credential
// session endpoint visible in the routes we inspected. Until that's
// wired for real, the tenant id is read from localStorage (set once after
// login, or via ?tenant=<id> for manual testing) rather than fabricated.
export function useTenant() {
  const [tenantId, setTenantId] = useState<string | null>(null);

  useEffect(() => {
    const url = new URL(window.location.href);
    const fromQuery = url.searchParams.get("tenant");
    if (fromQuery) {
      window.localStorage.setItem("techdeva_tenant_id", fromQuery);
      setTenantId(fromQuery);
      return;
    }
    setTenantId(window.localStorage.getItem("techdeva_tenant_id"));
  }, []);

  return tenantId;
}
