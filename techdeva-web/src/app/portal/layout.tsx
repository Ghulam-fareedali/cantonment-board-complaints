import { SidebarLayout } from "@/components/SidebarLayout";

const NAV = [
  { href: "/portal", label: "Overview" },
  { href: "/portal/onboarding", label: "Set up business" },
  { href: "/portal/leads", label: "Leads / CRM" },
  { href: "/portal/growth", label: "Growth plan" },
  { href: "/portal/reports", label: "Reports" },
  { href: "/portal/campaigns", label: "Campaigns" },
  { href: "/portal/billing", label: "Billing" },
  { href: "/portal/integrations", label: "Integrations" },
  { href: "/portal/settings", label: "Settings" },
];

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return <SidebarLayout title="Portal" nav={NAV}>{children}</SidebarLayout>;
}
