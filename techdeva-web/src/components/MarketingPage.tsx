import Link from "next/link";

export function MarketingPage({
  eyebrow,
  title,
  body,
  children,
}: {
  eyebrow: string;
  title: string;
  body: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-[var(--background)]">
      <header className="flex items-center justify-between border-b border-white/10 px-6 py-4">
        <Link href="/" className="flex items-center gap-2 text-base font-semibold text-white">
          <span className="h-2 w-2 rounded-full bg-[var(--accent)]" />
          TechDeva
        </Link>
        <Link href="/demo" className="td-btn-primary px-4 py-2 text-sm">
          Book a demo
        </Link>
      </header>
      <section className="mx-auto max-w-3xl px-6 py-20">
        <div className="text-sm font-medium uppercase tracking-wider text-[var(--accent)]">
          {eyebrow}
        </div>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          {title}
        </h1>
        <p className="mt-4 text-[var(--muted)]">{body}</p>
        {children}
      </section>
    </div>
  );
}
