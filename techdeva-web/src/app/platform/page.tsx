import { MarketingPage } from "@/components/MarketingPage";

export default function PlatformPage() {
  return (
    <MarketingPage
      eyebrow="Platform"
      title="One engine, every industry"
      body="Business rules — services, prices, persona, hours, departments — live in configuration, not code. Onboarding a new industry is a config row, never a rebuild. Every tenant is isolated at the database layer with row-level security; the app never connects as a superuser."
    >
      <ul className="mt-8 space-y-3 text-sm text-neutral-700 dark:text-neutral-300">
        <li>• Master Orchestrator — a rulebook and task router, not a chatbot.</li>
        <li>• Agent Contract — every AI employee reads config and events only; the orchestrator is the only thing with write access.</li>
        <li>• Human-in-the-loop enforced in code, not policy — money, publishing and pricing always park for approval.</li>
      </ul>
    </MarketingPage>
  );
}
