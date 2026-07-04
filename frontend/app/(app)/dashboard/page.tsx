"use client";

import { StatTile } from "@/components/stat-tile";
import { StatusDistribution } from "@/components/status-distribution";
import { ThroughputChart } from "@/components/throughput-chart";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { statsApi } from "@/lib/api";
import { usePolling } from "@/hooks/use-polling";

export default function DashboardPage() {
  const { data, error, loading } = usePolling(() => statsApi.overview(), 4000);

  if (loading) {
    return <p className="text-sm text-secondary">Loading cluster stats…</p>;
  }
  if (!data) {
    return (
      <p className="text-sm text-secondary">
        Could not load stats{error ? `: ${error}` : ""}. Is the API running?
      </p>
    );
  }

  const counts = data.counts_by_status;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Dashboard</h1>
        <p className="text-sm text-secondary">
          Cluster-wide view of the job scheduler.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatTile
          label="Ready now"
          value={data.ready_now}
          hint="pending, due to run"
        />
        <StatTile
          label="Scheduled"
          value={data.scheduled_later}
          hint="pending, future run_at"
        />
        <StatTile label="Running" value={counts.running ?? 0} />
        <StatTile label="Succeeded" value={counts.succeeded ?? 0} />
        <StatTile label="Dead (DLQ)" value={data.dlq_size} />
        <StatTile label="Workers online" value={data.workers_online} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Throughput — attempts finished per minute (last hour)</CardTitle>
          </CardHeader>
          <CardContent>
            <ThroughputChart data={data.throughput_last_hour} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Jobs by status</CardTitle>
          </CardHeader>
          <CardContent className="pt-2">
            <StatusDistribution counts={counts} />
          </CardContent>
        </Card>
      </div>

      {error && (
        <p className="text-xs text-muted">
          Live refresh failing ({error}); showing last good data.
        </p>
      )}
    </div>
  );
}
