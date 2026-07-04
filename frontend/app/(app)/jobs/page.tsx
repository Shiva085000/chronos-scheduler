"use client";

import { useState } from "react";
import Link from "next/link";
import { Plus, RotateCcw, XCircle } from "lucide-react";

import { NewJobDialog } from "@/components/new-job-dialog";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { jobsApi } from "@/lib/api";
import { shortId, timeAgo } from "@/lib/format";
import { usePolling } from "@/hooks/use-polling";

const STATUSES = ["", "pending", "running", "succeeded", "cancelled", "dead"];
const PAGE_SIZE = 25;

export default function JobsPage() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);

  const { data, refresh } = usePolling(
    () => jobsApi.list({ status: status || undefined, limit: PAGE_SIZE, offset }),
    4000,
    [status, offset],
  );

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Jobs</h1>
          <p className="text-sm text-secondary">
            {data ? `${data.total} job${data.total === 1 ? "" : "s"}` : "…"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
            aria-label="Filter by status"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s || "all statuses"}
              </option>
            ))}
          </Select>
          <Button onClick={() => setDialogOpen(true)}>
            <Plus className="h-4 w-4" aria-hidden /> New job
          </Button>
        </div>
      </div>

      <Card>
        <Table>
          <THead>
            <TR>
              <TH>Job</TH>
              <TH>Task</TH>
              <TH>Status</TH>
              <TH>Attempts</TH>
              <TH>Priority</TH>
              <TH>Created</TH>
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
                <TD className="tabular-nums">{job.priority}</TD>
                <TD className="text-xs text-secondary">{timeAgo(job.created_at)}</TD>
                <TD className="text-right">
                  {job.status === "pending" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => act(() => jobsApi.cancel(job.id))}
                    >
                      <XCircle className="h-3.5 w-3.5" aria-hidden /> Cancel
                    </Button>
                  )}
                  {(job.status === "dead" || job.status === "cancelled") && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => act(() => jobsApi.requeue(job.id))}
                    >
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Requeue
                    </Button>
                  )}
                </TD>
              </TR>
            ))}
            {data && data.items.length === 0 && (
              <TR>
                <TD colSpan={7} className="py-8 text-center text-sm text-muted">
                  No jobs yet — enqueue one with “New job”.
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Card>

      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <Button
            variant="outline"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            Previous
          </Button>
          <span className="text-xs text-secondary">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={offset + PAGE_SIZE >= data.total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      )}

      <NewJobDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onCreated={refresh}
      />
    </div>
  );
}
