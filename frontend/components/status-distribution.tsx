"use client";

/* Horizontal magnitude bars, one per status. Built in plain HTML — five
   values don't need a charting library. Each row carries its label and
   count as text; the colored bar is reinforcement, not the only channel. */
export function StatusDistribution({
  counts,
}: {
  counts: Record<string, number>;
}) {
  const order: { key: string; color: string }[] = [
    { key: "pending", color: "var(--viz-pending)" },
    { key: "running", color: "var(--viz-running)" },
    { key: "succeeded", color: "var(--viz-succeeded)" },
    { key: "cancelled", color: "var(--viz-cancelled)" },
    { key: "dead", color: "var(--viz-dead)" },
  ];
  const max = Math.max(1, ...order.map(({ key }) => counts[key] ?? 0));

  return (
    <div className="space-y-2.5">
      {order.map(({ key, color }) => {
        const value = counts[key] ?? 0;
        return (
          <div key={key} className="flex items-center gap-3 text-sm">
            <span className="w-20 shrink-0 text-xs text-secondary">{key}</span>
            <div className="h-4 flex-1 overflow-hidden rounded-[4px]">
              <div
                className="h-full rounded-[4px] transition-all"
                style={{
                  width: `${Math.max(value > 0 ? 2 : 0, (value / max) * 100)}%`,
                  backgroundColor: color,
                }}
                title={`${key}: ${value}`}
              />
            </div>
            <span className="w-12 shrink-0 text-right text-xs tabular-nums text-foreground">
              {value}
            </span>
          </div>
        );
      })}
    </div>
  );
}
