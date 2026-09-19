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
    <div className="min-h-screen bg-white dark:bg-neutral-950">
      <header className="flex items-center justify-between border-b border-black/10 px-6 py-4 dark:border-white/10">
        <span className="text-lg font-semibold">TechDeva</span>
        <nav className="hidden gap-6 text-sm text-neutral-600 dark:text-neutral-300 md:flex">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="hover:text-neutral-900 dark:hover:text-white">
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="flex gap-3 text-sm">
          <Link href="/login" className="rounded-lg px-3 py-2 hover:bg-neutral-100 dark:hover:bg-neutral-800">
            Log in
          </Link>
          <Link
            href="/demo"
            className="rounded-lg bg-neutral-900 px-4 py-2 font-medium text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900"
          >
            Book a demo
          </Link>
        </div>
      </header>

      <section className="mx-auto max-w-4xl px-6 py-24 text-center">
        <h1 className="text-4xl font-semibold tracking-tight text-neutral-900 dark:text-white sm:text-5xl">
          The AI Business Operating System
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-lg text-neutral-600 dark:text-neutral-300">
          One platform, one AI workforce, every business function — leads, bookings,
          reporting, campaigns — running on autopilot with a human always in
          control of the decisions that matter.
        </p>
        <div className="mt-8 flex justify-center gap-4">
          <Link
            href="/demo"
            className="rounded-lg bg-neutral-900 px-6 py-3 font-medium text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900"
          >
            See it in action
          </Link>
          <Link
            href="/platform"
            className="rounded-lg border border-black/10 px-6 py-3 font-medium text-neutral-900 hover:bg-neutral-50 dark:border-white/10 dark:text-white dark:hover:bg-neutral-900"
          >
            Explore the platform
          </Link>
        </div>
      </section>

      <section className="mx-auto grid max-w-5xl gap-6 px-6 pb-24 sm:grid-cols-3">
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
          <div key={f.title} className="rounded-xl border border-black/10 p-6 dark:border-white/10">
            <h3 className="font-semibold text-neutral-900 dark:text-white">{f.title}</h3>
            <p className="mt-2 text-sm text-neutral-600 dark:text-neutral-300">{f.body}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
