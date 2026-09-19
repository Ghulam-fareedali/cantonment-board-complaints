"use client";

import { useEffect, useState } from "react";
import { getAiHealth, type AiHealth } from "@/lib/api";

export default function AiHealthPage() {
  const [health, setHealth] = useState<AiHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAiHealth().then((r) => (r.ok ? setHealth(r.data) : setError(r.error)));
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold text-white">AI Health</h1>
      {error ? (
        <p className="mt-4 text-sm text-[var(--danger)]">Could not load: {error}</p>
      ) : (
        <pre className="td-card mt-4 p-4 text-xs text-[var(--muted)]">
          {health ? JSON.stringify(health, null, 2) : "Loading…"}
        </pre>
      )}
    </div>
  );
}
