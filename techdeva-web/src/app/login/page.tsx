"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { API_BASE } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      // NOTE: the platform's actual auth endpoint/shape should be confirmed
      // against app/main.py before wiring this for real — placeholder wired
      // to /oauth flows exists server-side, but no plain-credential login
      // route was found during this build. This POST target is a stand-in
      // until that's confirmed.
      const res = await fetch(`${API_BASE}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
        }),
      });
      if (!res.ok) {
        setError("Could not sign in — check your details and try again.");
        setLoading(false);
        return;
      }
      router.push("/portal");
    } catch {
      setError("Could not reach the server.");
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-neutral-50 px-6 dark:bg-neutral-950">
      <div className="w-full max-w-sm rounded-xl border border-black/10 bg-white p-8 shadow-sm dark:border-white/10 dark:bg-neutral-900">
        <Link href="/" className="text-lg font-semibold text-neutral-900 dark:text-white">
          TechDeva
        </Link>
        <h1 className="mt-4 text-xl font-semibold text-neutral-900 dark:text-white">Log in</h1>
        <form onSubmit={onSubmit} className="mt-6 grid gap-3">
          <input
            name="email"
            type="email"
            required
            placeholder="Email"
            className="rounded-lg border border-black/10 px-3 py-2 text-sm dark:border-white/10 dark:bg-neutral-800"
          />
          <input
            name="password"
            type="password"
            required
            placeholder="Password"
            className="rounded-lg border border-black/10 px-3 py-2 text-sm dark:border-white/10 dark:bg-neutral-800"
          />
          {error ? <p className="text-sm text-red-600">{error}</p> : null}
          <button
            type="submit"
            disabled={loading}
            className="mt-2 rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {loading ? "Signing in…" : "Log in"}
          </button>
        </form>
      </div>
    </div>
  );
}
