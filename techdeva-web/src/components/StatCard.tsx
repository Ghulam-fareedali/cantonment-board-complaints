// Renders "—" for null/undefined instead of a fabricated 0. See Constitution
// invariant #2 ("Null, never zero") — a missing figure must read as missing.
export function StatCard({
  label,
  value,
  suffix,
  hint,
}: {
  label: string;
  value: number | string | null | undefined;
  suffix?: string;
  hint?: string;
}) {
  const display =
    value === null || value === undefined || value === ""
      ? "—"
      : typeof value === "number"
        ? value.toLocaleString()
        : value;
  const isMissing = display === "—";

  return (
    <div className="td-card p-5 transition-colors">
      <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">
        {label}
      </div>
      <div
        className={`mt-3 text-3xl font-semibold tracking-tight ${
          isMissing ? "text-[var(--muted)]" : "text-white"
        }`}
      >
        {display}
        {suffix && !isMissing ? (
          <span className="ml-1 text-base font-normal text-[var(--muted)]">{suffix}</span>
        ) : null}
      </div>
      {hint ? <div className="mt-2 text-xs text-[var(--muted)]">{hint}</div> : null}
    </div>
  );
}
