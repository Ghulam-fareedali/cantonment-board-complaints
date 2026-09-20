"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import { API_BASE } from "@/lib/api";

/** Choose a first password from a one-time setup link. */
function Form() {
  const token = useSearchParams().get("token") ?? "";
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit() {
    setError(null);
    if (pw.length < 10) return setError("Use at least 10 characters.");
    if (pw !== again) return setError("The two passwords do not match.");
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/auth/set-password`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password: pw }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) setError(data?.detail ?? "Could not set your password.");
      else setDone(true);
    } catch {
      setError("Network error — nothing was changed.");
    }
    setBusy(false);
  }

  if (!token) {
    return <p className="text-sm text-[var(--muted)]">This link is missing its token.</p>;
  }

  if (done) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-white">Password set</h1>
        <p className="mt-3 text-sm text-[var(--muted)]">
          You can now{" "}
          <Link className="underline" href="/login">
            sign in
          </Link>
          . This link will not work again.
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-xl font-semibold text-white">Choose your password</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">
        At least 10 characters. Only you ever see it.
      </p>
      <div className="mt-6 space-y-3">
        <input
          type="password"
          autoComplete="new-password"
          placeholder="New password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          className="w-full rounded border border-white/10 bg-black/30 px-3 py-2 text-sm text-white focus:border-white/30 focus:outline-none"
        />
        <input
          type="password"
          autoComplete="new-password"
          placeholder="Repeat password"
          value={again}
          onChange={(e) => setAgain(e.target.value)}
          className="w-full rounded border border-white/10 bg-black/30 px-3 py-2 text-sm text-white focus:border-white/30 focus:outline-none"
        />
      </div>
      {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
      <button
        type="button"
        disabled={busy}
        onClick={() => void submit()}
        className="mt-5 rounded bg-white/90 px-4 py-2 text-sm font-medium text-black disabled:opacity-50"
      >
        {busy ? "Saving…" : "Set password"}
      </button>
    </div>
  );
}

export default function SetPasswordPage() {
  return (
    <main className="mx-auto max-w-md px-6 py-20">
      <Suspense fallback={<p className="text-sm text-[var(--muted)]">Loading…</p>}>
        <Form />
      </Suspense>
    </main>
  );
}
