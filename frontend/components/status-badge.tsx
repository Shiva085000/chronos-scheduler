import {
  Ban,
  CheckCircle2,
  CircleDashed,
  Loader2,
  Skull,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

/* Status is never conveyed by color alone: every badge pairs an icon and
   a text label with its wash. */
const styles: Record<string, { color: string; wash: string; icon: LucideIcon }> = {
  pending: { color: "var(--viz-pending)", wash: "var(--wash-pending)", icon: CircleDashed },
  running: { color: "var(--viz-running)", wash: "var(--wash-running)", icon: Loader2 },
  succeeded: { color: "var(--viz-succeeded)", wash: "var(--wash-succeeded)", icon: CheckCircle2 },
  cancelled: { color: "var(--viz-cancelled)", wash: "var(--wash-cancelled)", icon: Ban },
  dead: { color: "var(--viz-dead)", wash: "var(--wash-failed)", icon: Skull },
  // attempt statuses
  failed: { color: "var(--viz-failed)", wash: "var(--wash-failed)", icon: Skull },
  lost: { color: "var(--viz-failed)", wash: "var(--wash-failed)", icon: CircleDashed },
  aborted: { color: "var(--viz-cancelled)", wash: "var(--wash-cancelled)", icon: Ban },
  online: { color: "var(--viz-succeeded)", wash: "var(--wash-succeeded)", icon: CheckCircle2 },
  draining: { color: "var(--viz-pending)", wash: "var(--wash-pending)", icon: Loader2 },
  offline: { color: "var(--viz-cancelled)", wash: "var(--wash-cancelled)", icon: Ban },
};

export function StatusBadge({ status }: { status: string }) {
  const style = styles[status] ?? styles.cancelled;
  const Icon = style.icon;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium text-foreground"
      style={{ backgroundColor: style.wash }}
    >
      <Icon
        className={cn("h-3 w-3", status === "running" && "animate-spin")}
        style={{ color: style.color }}
        aria-hidden
      />
      {status}
    </span>
  );
}
