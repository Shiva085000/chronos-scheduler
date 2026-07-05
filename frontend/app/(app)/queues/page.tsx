"use client";

import { useState } from "react";
import { Pause, Play, Settings2 } from "lucide-react";

import { QueueConfigDialog } from "@/components/queue-config-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { QueueInfo, queuesApi } from "@/lib/api";
import { usePolling } from "@/hooks/use-polling";
import { cn } from "@/lib/utils";

export default function QueuesPage() {
  const { data, refresh } = usePolling(() => queuesApi.list(), 4000);
  const [editing, setEditing] = useState<QueueInfo | null>(null);

  const togglePause = async (queue: QueueInfo) => {
    try {
      await (queue.paused ? queuesApi.resume(queue.id) : queuesApi.pause(queue.id));
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Queues</h1>
        <p className="text-sm text-secondary">
          Pausing stops new claims immediately (running jobs finish); a
          concurrency cap bounds how many of a queue&apos;s jobs run
          fleet-wide. Defaults apply to future jobs that don&apos;t set
          their own.
        </p>
      </div>

      <Card>
        <Table>
          <THead>
            <TR>
              <TH>Queue</TH>
              <TH>State</TH>
              <TH>Shard</TH>
              <TH>Cap</TH>
              <TH>Pending</TH>
              <TH>Running</TH>
              <TH>Succeeded</TH>
              <TH>Dead</TH>
              <TH>Retry default</TH>
              <TH className="text-right">Actions</TH>
            </TR>
          </THead>
          <TBody>
            {data?.map((queue) => (
              <TR key={queue.id}>
                <TD className="font-mono text-xs">{queue.name}</TD>
                <TD>
                  <span
                    className={cn(
                      "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                      queue.paused
                        ? "bg-[var(--wash-failed)]"
                        : "bg-[var(--wash-succeeded)]",
                    )}
                  >
                    {queue.paused ? "paused" : "active"}
                  </span>
                </TD>
                <TD className="tabular-nums">
                  {queue.shard_key > 0 ? (
                    <span className="inline-flex items-center rounded-full bg-indigo-500/10 px-2 py-0.5 text-xs font-medium text-indigo-400">
                      #{queue.shard_key}
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </TD>
                <TD className="tabular-nums">
                  {queue.max_concurrency ?? "∞"}
                </TD>
                <TD className="tabular-nums">{queue.counts_by_status.pending}</TD>
                <TD className="tabular-nums">{queue.counts_by_status.running}</TD>
                <TD className="tabular-nums">{queue.counts_by_status.succeeded}</TD>
                <TD className="tabular-nums">{queue.counts_by_status.dead}</TD>
                <TD className="text-xs text-secondary">
                  {queue.default_backoff_strategy} ×{queue.default_max_attempts},{" "}
                  {queue.default_backoff_base_seconds}s base
                </TD>
                <TD className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => togglePause(queue)}
                    >
                      {queue.paused ? (
                        <>
                          <Play className="h-3.5 w-3.5" aria-hidden /> Resume
                        </>
                      ) : (
                        <>
                          <Pause className="h-3.5 w-3.5" aria-hidden /> Pause
                        </>
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setEditing(queue)}
                    >
                      <Settings2 className="h-3.5 w-3.5" aria-hidden /> Configure
                    </Button>
                  </div>
                </TD>
              </TR>
            ))}
            {data && data.length === 0 && (
              <TR>
                <TD colSpan={9} className="py-8 text-center text-sm text-muted">
                  No queues yet — one is created automatically the first time
                  you enqueue a job.
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Card>

      <QueueConfigDialog
        queue={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          refresh();
        }}
      />
    </div>
  );
}
