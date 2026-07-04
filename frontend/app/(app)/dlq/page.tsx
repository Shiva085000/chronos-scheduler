"use client";

import Link from "next/link";
import { RotateCcw } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { dlqApi } from "@/lib/api";
import { shortId, timeAgo } from "@/lib/format";
import { usePolling } from "@/hooks/use-polling";

export default function DlqPage() {
  const { data, refresh } = usePolling(() => dlqApi.list(), 5000);

  const requeue = async (id: string) => {
    try {
      await dlqApi.requeue(id);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Dead Letter Queue</h1>
        <p className="text-sm text-secondary">
          Jobs that exhausted their retry budget. Inspect the error, fix the
          cause, then requeue with a fresh attempt budget.
        </p>
      </div>

      <Card>
        <Table>
          <THead>
            <TR>
              <TH>Job</TH>
              <TH>Task</TH>
              <TH>Status</TH>
              <TH>Attempts</TH>
              <TH>Last error</TH>
              <TH>Died</TH>
              <TH className="text-right">Actions</TH>
            </TR>
          </THead>
          <TBody>
            {data?.items.map((job) => (
              <TR key={job.id}>
                <TD>
                  <Link
                    href={`/jobs/${job.id}`}
                    className="font-mono text-xs text-accent hover:underline"
                  >
                    {shortId(job.id)}
                  </Link>
                </TD>
                <TD className="font-mono text-xs">{job.task_name}</TD>
                <TD>
                  <StatusBadge status={job.status} />
                </TD>
                <TD className="tabular-nums">
                  {job.attempt_count}/{job.max_attempts}
                </TD>
                <TD className="max-w-sm truncate text-xs text-secondary">
                  {job.last_error
                    ? job.last_error.split("\n").filter(Boolean).slice(-1)[0]
                    : "—"}
                </TD>
                <TD className="text-xs text-secondary">{timeAgo(job.finished_at)}</TD>
                <TD className="text-right">
                  <Button variant="ghost" size="sm" onClick={() => requeue(job.id)}>
                    <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Requeue
                  </Button>
                </TD>
              </TR>
            ))}
            {data && data.items.length === 0 && (
              <TR>
                <TD colSpan={7} className="py-8 text-center text-sm text-muted">
                  The dead letter queue is empty. 🎉
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Card>
    </div>
  );
}
