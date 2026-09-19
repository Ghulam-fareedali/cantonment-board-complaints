"use client";

import { useState } from "react";
import { MarketingPage } from "@/components/MarketingPage";
import { API_BASE } from "@/lib/api";

export default function DemoPage() {
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setStatus("sending");
    try {
      const res = await fetch(`${API_BASE}/client-request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.get("name"),
          business: form.get("business"),
          phone: form.get("phone"),
          email: form.get("email"),
        }),
      });
      setStatus(res.ok ? "sent" : "error");
    } catch {
      setStatus("error");
    }
  }

  return (
    <MarketingPage
      eyebrow="Demo"
      title="Book a demo"
      body="Tell us about your business — we'll reach out to schedule a walkthrough."
    >
      {status === "sent" ? (
        <p className="mt-8 text-sm text-[var(--success)]">
          Thanks — we&apos;ve got your request and will be in touch.
        </p>
      ) : (
        <form onSubmit={onSubmit} className="mt-8 grid gap-4 sm:max-w-md">
          <input
            name="name"
            required
            placeholder="Your name"
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none"
          />
          <input
            name="business"
            required
            placeholder="Business name"
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none"
          />
          <input
            name="phone"
            placeholder="Phone"
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none"
          />
          <input
            name="email"
            type="email"
            placeholder="Email"
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none"
          />
          <button type="submit" disabled={status === "sending"} className="td-btn-primary px-4 py-2 text-sm disabled:opacity-50">
            {status === "sending" ? "Sending…" : "Request a demo"}
          </button>
          {status === "error" ? (
            <p className="text-sm text-[var(--danger)]">Something went wrong — please try again.</p>
          ) : null}
        </form>
      )}
    </MarketingPage>
  );
}
