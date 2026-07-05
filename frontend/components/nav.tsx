"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  CalendarClock,
  Cpu,
  Layers,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Shield,
  Skull,
} from "lucide-react";

import { authApi, clearToken, type UserRole } from "@/lib/api";
import { cn } from "@/lib/utils";

const links = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/queues", label: "Queues", icon: Layers },
  { href: "/jobs", label: "Jobs", icon: ListTodo },
  { href: "/schedules", label: "Schedules", icon: CalendarClock },
  { href: "/dlq", label: "Dead Letter Queue", icon: Skull },
  { href: "/workers", label: "Workers", icon: Cpu },
];

const roleBadgeColor: Record<UserRole, string> = {
  owner: "bg-purple-500/10 text-purple-400",
  admin: "bg-blue-500/10 text-blue-400",
  member: "bg-green-500/10 text-green-400",
  viewer: "bg-gray-500/10 text-gray-400",
};

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [role, setRole] = useState<UserRole | null>(null);
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    authApi.me().then((u) => { setRole(u.role); setEmail(u.email); }).catch(() => {});
  }, []);

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

      {/* User role badge */}
      {role && (
        <div className="mx-3 mb-2 flex items-center gap-2 rounded-md px-3 py-2">
          <Shield className="h-3.5 w-3.5 text-muted" aria-hidden />
          <span className="truncate text-xs text-secondary">{email}</span>
          <span
            className={cn(
              "ml-auto inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
              roleBadgeColor[role],
            )}
          >
            {role}
          </span>
        </div>
      )}

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
