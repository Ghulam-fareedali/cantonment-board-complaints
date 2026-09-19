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
      <h1 className="text-xl font-semibold text-neutral-900 dark:text-white">AI Health</h1>
      {error ? (
        <p className="mt-4 text-sm text-red-600">Could not load: {error}</p>
      ) : (
        <pre className="mt-4 rounded-xl border border-black/10 bg-neutral-50 p-4 text-xs dark:border-white/10 dark:bg-neutral-900">
          {health ? JSON.stringify(health, null, 2) : "Loading…"}
        </pre>
      )}
    </div>
  );
}
