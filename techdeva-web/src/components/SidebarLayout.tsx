import Link from "next/link";

export function SidebarLayout({
  title,
  nav,
  children,
}: {
  title: string;
  nav: { href: string; label: string }[];
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-neutral-50 dark:bg-neutral-950 lg:grid lg:grid-cols-[220px_1fr]">
      <aside className="border-b border-black/10 bg-white p-4 dark:border-white/10 dark:bg-neutral-900 lg:min-h-screen lg:border-b-0 lg:border-r">
        <div className="mb-6 px-2 text-lg font-semibold text-neutral-900 dark:text-neutral-50">
          {title}
        </div>
        <nav className="flex flex-col gap-1">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-lg px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="p-6">{children}</main>
    </div>
  );
}
