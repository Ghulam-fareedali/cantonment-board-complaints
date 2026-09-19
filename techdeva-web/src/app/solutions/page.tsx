import { MarketingPage } from "@/components/MarketingPage";

export default function SolutionsPage() {
  return (
    <MarketingPage
      eyebrow="Solutions"
      title="Built for service businesses"
      body="Aesthetic clinics, salons, academies, and beyond — the same platform, configured per business. The lab is one real clinic, proven in production, before anything scales."
    >
      <ul className="mt-8 space-y-3 text-sm text-[var(--muted)]">
        <li>• Clinics &amp; aesthetic lounges</li>
        <li>• Salons</li>
        <li>• Academies &amp; training businesses</li>
        <li>• Any appointment-driven service business</li>
      </ul>
    </MarketingPage>
  );
}
