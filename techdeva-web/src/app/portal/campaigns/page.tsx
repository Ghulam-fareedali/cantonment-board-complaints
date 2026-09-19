export default function CampaignsPage() {
  return (
    <div>
      <h1 className="text-xl font-semibold text-white">Campaigns</h1>
      <p className="mt-4 text-sm text-[var(--muted)]">
        Ad campaigns always park for your approval before spend goes live — that gate is
        enforced in the platform&apos;s policy layer, not just in this UI. A read/approve view
        wires to the orchestrator&apos;s task queue for campaign-type tasks; connect it once
        that endpoint is confirmed.
      </p>
    </div>
  );
}
