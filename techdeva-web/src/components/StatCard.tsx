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

  return (
    <div className="rounded-xl border border-black/10 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-neutral-900">
      <div className="text-xs font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold text-neutral-900 dark:text-neutral-50">
        {display}
        {suffix && display !== "—" ? (
          <span className="ml-1 text-base font-normal text-neutral-500">{suffix}</span>
        ) : null}
      </div>
      {hint ? <div className="mt-1 text-xs text-neutral-500">{hint}</div> : null}
    </div>
  );
}
