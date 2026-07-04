"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ThroughputPoint } from "@/lib/api";
import { clockTime } from "@/lib/format";

/* Two series (succeeded / failed per minute) on one shared y-axis.
   Colors are the validated palette's aqua and red slots; the legend plus
   tooltip labels carry identity, never color alone. */
export function ThroughputChart({ data }: { data: ThroughputPoint[] }) {
  const points = data.map((p) => ({ ...p, label: clockTime(p.minute) }));
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={points} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid stroke="var(--viz-grid)" strokeDasharray="0" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: "var(--viz-axis)", fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: "var(--viz-grid)" }}
          interval="preserveStartEnd"
          minTickGap={48}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fill: "var(--viz-axis)", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={48}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "var(--surface)",
            border: "1px solid var(--border-line)",
            borderRadius: 8,
            fontSize: 12,
            color: "var(--foreground)",
          }}
          labelStyle={{ color: "var(--secondary-ink)" }}
          cursor={{ stroke: "var(--viz-axis)", strokeWidth: 1 }}
        />
        <Legend
          iconType="plainline"
          wrapperStyle={{ fontSize: 12, color: "var(--secondary-ink)" }}
        />
        <Area
          type="monotone"
          dataKey="succeeded"
          name="succeeded / min"
          stroke="var(--viz-succeeded)"
          strokeWidth={2}
          fill="var(--viz-succeeded)"
          fillOpacity={0.12}
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Area
          type="monotone"
          dataKey="failed"
          name="failed / min"
          stroke="var(--viz-failed)"
          strokeWidth={2}
          fill="var(--viz-failed)"
          fillOpacity={0.12}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
