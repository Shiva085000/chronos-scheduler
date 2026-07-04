"use client";

import { StatusBadge } from "@/components/status-badge";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { workersApi } from "@/lib/api";
import { shortId, timeAgo } from "@/lib/format";
import { usePolling } from "@/hooks/use-polling";

export default function WorkersPage() {
  const { data } = usePolling(() => workersApi.list(), 4000);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Workers</h1>
        <p className="text-sm text-secondary">
          Fleet liveness. A worker missing heartbeats for 60s is declared
          offline by the reaper and its leases are reclaimed.
        </p>
      </div>

      <Card>
        <Table>
          <THead>
            <TR>
              <TH>Worker</TH>
              <TH>Name</TH>
              <TH>Status</TH>
              <TH>Concurrency</TH>
              <TH>Last heartbeat</TH>
              <TH>Started</TH>
              <TH>Stopped</TH>
            </TR>
          </THead>
          <TBody>
            {data?.map((worker) => (
              <TR key={worker.id}>
                <TD className="font-mono text-xs">{shortId(worker.id)}</TD>
                <TD className="font-mono text-xs">{worker.name}</TD>
                <TD>
                  <StatusBadge status={worker.status} />
                </TD>
                <TD className="tabular-nums">{worker.concurrency}</TD>
                <TD className="text-xs text-secondary">
                  {timeAgo(worker.last_heartbeat_at)}
                </TD>
                <TD className="text-xs text-secondary">{timeAgo(worker.started_at)}</TD>
                <TD className="text-xs text-secondary">{timeAgo(worker.stopped_at)}</TD>
              </TR>
            ))}
            {data && data.length === 0 && (
              <TR>
                <TD colSpan={7} className="py-8 text-center text-sm text-muted">
                  No workers have registered yet.
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Card>
    </div>
  );
}
