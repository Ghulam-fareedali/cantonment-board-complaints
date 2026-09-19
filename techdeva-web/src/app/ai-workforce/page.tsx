import { MarketingPage } from "@/components/MarketingPage";

const AGENTS = [
  ["Hermes", "WhatsApp, CRM, leads, appointments, customer communication"],
  ["Analyzer", "Business intelligence and performance analysis"],
  ["Designer", "On-brand creative assets"],
  ["Marketing Expert", "Campaign strategy"],
  ["Campaign Manager", "Ad execution, gated on spend approval"],
  ["Social Media Expert", "Publishing, gated on approval"],
  ["Reporting", "ROAS, CPL, attribution — null when there's no data, never a fabricated zero"],
];

export default function AiWorkforcePage() {
  return (
    <MarketingPage
      eyebrow="AI Workforce"
      title="Employees, not features"
      body="Every AI employee has a mission, permissions, restrictions, KPIs and memory access — and follows the same constitution. None of them can change pricing, memory, or spend money without your approval."
    >
      <div className="mt-8 divide-y divide-black/10 dark:divide-white/10">
        {AGENTS.map(([name, desc]) => (
          <div key={name} className="flex flex-col gap-1 py-4 sm:flex-row sm:items-baseline sm:justify-between">
            <span className="font-medium text-neutral-900 dark:text-white">{name}</span>
            <span className="text-sm text-neutral-600 dark:text-neutral-300">{desc}</span>
          </div>
        ))}
      </div>
    </MarketingPage>
  );
}
