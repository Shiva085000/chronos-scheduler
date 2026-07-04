"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Cpu,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Skull,
} from "lucide-react";

import { clearToken } from "@/lib/api";
import { cn } from "@/lib/utils";

const links = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/jobs", label: "Jobs", icon: ListTodo },
  { href: "/dlq", label: "Dead Letter Queue", icon: Skull },
  { href: "/workers", label: "Workers", icon: Cpu },
];

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2 px-5 py-5">
        <Activity className="h-5 w-5 text-accent" aria-hidden />
        <span className="text-sm font-semibold tracking-tight">Chronos</span>
        <span className="text-xs text-muted">scheduler</span>
      </div>
      <nav className="flex-1 space-y-1 px-3">
        {links.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-secondary hover:bg-line/40",
              pathname.startsWith(href) && "bg-line/50 font-medium text-foreground",
            )}
          >
            <Icon className="h-4 w-4" aria-hidden />
            {label}
          </Link>
        ))}
      </nav>
      <button
        onClick={() => {
          clearToken();
          router.push("/login");
        }}
        className="mx-3 mb-4 flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-secondary hover:bg-line/40"
      >
        <LogOut className="h-4 w-4" aria-hidden />
        Log out
      </button>
    </aside>
  );
}
