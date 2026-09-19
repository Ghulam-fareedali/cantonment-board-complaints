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
    <div className="min-h-screen bg-white dark:bg-neutral-950">
      <header className="flex items-center justify-between border-b border-black/10 px-6 py-4 dark:border-white/10">
        <Link href="/" className="text-lg font-semibold">
          TechDeva
        </Link>
        <Link href="/demo" className="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-neutral-900">
          Book a demo
        </Link>
      </header>
      <section className="mx-auto max-w-3xl px-6 py-20">
        <div className="text-sm font-medium uppercase tracking-wide text-neutral-500">{eyebrow}</div>
        <h1 className="mt-2 text-3xl font-semibold text-neutral-900 dark:text-white">{title}</h1>
        <p className="mt-4 text-neutral-600 dark:text-neutral-300">{body}</p>
        {children}
      </section>
    </div>
  );
}
