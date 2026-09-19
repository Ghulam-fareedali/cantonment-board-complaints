"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function SidebarLayout({
  title,
  nav,
  children,
}: {
  title: string;
  nav: { href: string; label: string }[];
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-[var(--background)] lg:grid lg:grid-cols-[240px_1fr]">
      <aside className="border-b border-white/10 bg-[var(--surface)]/60 p-5 backdrop-blur lg:min-h-screen lg:border-b-0 lg:border-r">
        <div className="mb-8 flex items-center gap-2 px-1">
          <span className="h-2 w-2 rounded-full bg-[var(--accent)]" />
          <span className="text-base font-semibold tracking-tight text-white">{title}</span>
        </div>
        <nav className="flex flex-col gap-1">
          {nav.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-lg px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-[var(--accent-soft)] font-medium text-[var(--accent)]"
                    : "text-[var(--muted)] hover:bg-white/5 hover:text-white"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <main className="p-6 lg:p-8">{children}</main>
    </div>
  );
}
