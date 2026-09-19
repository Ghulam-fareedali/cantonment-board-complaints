import Link from "next/link";

const NAV = [
  { href: "/platform", label: "Platform" },
  { href: "/ai-workforce", label: "AI Workforce" },
  { href: "/solutions", label: "Solutions" },
  { href: "/pricing", label: "Pricing" },
  { href: "/case-studies", label: "Case Studies" },
];

export default function Home() {
  return (
    <div className="min-h-screen bg-[var(--background)]">
      <header className="flex items-center justify-between border-b border-white/10 px-6 py-4">
        <span className="flex items-center gap-2 text-base font-semibold text-white">
          <span className="h-2 w-2 rounded-full bg-[var(--accent)]" />
          TechDeva
        </span>
        <nav className="hidden gap-7 text-sm text-[var(--muted)] md:flex">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="transition-colors hover:text-white">
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2 text-sm">
          <Link href="/login" className="td-btn-ghost px-3 py-2">
            Log in
          </Link>
          <Link href="/demo" className="td-btn-primary px-4 py-2">
            Book a demo
          </Link>
        </div>
      </header>

      <section className="mx-auto max-w-4xl px-6 py-28 text-center">
        <div className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-[var(--muted)]">
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--success)]" />
          Live in production — The Beauty Aesthetic &amp; Lounge
        </div>
        <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-6xl">
          The AI Business
          <br />
          Operating System
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-[var(--muted)]">
          One platform, one AI workforce, every business function — leads, bookings,
          reporting, campaigns — running on autopilot with a human always in
          control of the decisions that matter.
        </p>
        <div className="mt-9 flex justify-center gap-3">
          <Link href="/demo" className="td-btn-primary px-6 py-3 text-sm font-medium">
            See it in action
          </Link>
          <Link href="/platform" className="td-btn-ghost px-6 py-3 text-sm font-medium">
            Explore the platform
          </Link>
        </div>
      </section>

      <section className="mx-auto grid max-w-5xl gap-5 px-6 pb-28 sm:grid-cols-3">
        {[
          {
            title: "Not a chatbot",
            body: "Hermes is Employee #1, not the product. The platform is the operating system your whole business runs on.",
          },
          {
            title: "Human in the loop",
            body: "Money, publishing, pricing and permissions always require your sign-off. Everything else runs autonomously.",
          },
          {
            title: "One engine, any industry",
            body: "Salon, clinic, academy — the same engine, configured per business. Onboarding is a config row, not new code.",
          },
        ].map((f) => (
          <div key={f.title} className="td-card p-6">
            <h3 className="font-semibold text-white">{f.title}</h3>
            <p className="mt-2 text-sm text-[var(--muted)]">{f.body}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
