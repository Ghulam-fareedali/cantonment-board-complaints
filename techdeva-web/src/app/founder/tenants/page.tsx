export default function TenantsPage() {
  return (
    <div>
      <h1 className="text-xl font-semibold text-neutral-900 dark:text-white">Tenants</h1>
      <p className="mt-4 text-sm text-neutral-600 dark:text-neutral-300">
        No per-tenant list endpoint was found on the API during this build (
        <code>/techdeva/overview</code> returns aggregate counts only). Add a
        <code> /techdeva/tenants</code> route that returns id, name and status per tenant,
        then wire this page to it — don&apos;t fabricate a tenant list here.
      </p>
    </div>
  );
}
