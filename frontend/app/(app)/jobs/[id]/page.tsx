"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, RotateCcw, XCircle } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { jobsApi } from "@/lib/api";
import { duration, shortId, timeAgo } from "@/lib/format";
import { usePolling } from "@/hooks/use-polling";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-muted">
        {label}
      </p>
      <div className="mt-0.5 text-sm">{children}</div>
    </div>
  );
}

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;

  const { data, refresh } = usePolling(
    async () => {
      const [job, attempts] = await Promise.all([
        jobsApi.get(jobId),
        jobsApi.attempts(jobId),
      ]);
      return { job, attempts };
    },
    3000,
    [jobId],
  );

  if (!data) return <p className="text-sm text-secondary">Loading job…</p>;
  const { job, attempts } = data;

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
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/jobs" className="text-secondary hover:text-foreground">
            <ArrowLeft className="h-4 w-4" aria-label="Back to jobs" />
          </Link>
          <div>
            <h1 className="font-mono text-lg font-semibold">{shortId(job.id)}</h1>
            <p className="font-mono text-xs text-secondary">{job.id}</p>
          </div>
          <StatusBadge status={job.status} />
        </div>
        <div className="flex gap-2">
          {job.status === "pending" && (
            <Button variant="outline" onClick={() => act(() => jobsApi.cancel(job.id))}>
              <XCircle className="h-4 w-4" aria-hidden /> Cancel
            </Button>
          )}
          {(job.status === "dead" || job.status === "cancelled") && (
            <Button onClick={() => act(() => jobsApi.requeue(job.id))}>
              <RotateCcw className="h-4 w-4" aria-hidden /> Requeue
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardContent className="grid grid-cols-2 gap-x-6 gap-y-4 pt-4 md:grid-cols-4">
          <Field label="Task">
            <span className="font-mono text-xs">{job.task_name}</span>
          </Field>
          <Field label="Queue">{job.queue}</Field>
          <Field label="Priority">{job.priority}</Field>
          <Field label="Attempts">
            {job.attempt_count} of {job.max_attempts}
          </Field>
          <Field label="Created">{timeAgo(job.created_at)}</Field>
          <Field label="Run at">{timeAgo(job.run_at)}</Field>
          <Field label="Finished">{timeAgo(job.finished_at)}</Field>
          <Field label="Lease expires">
            {job.status === "running" ? timeAgo(job.lease_expires_at) : "—"}
          </Field>
          <Field label="Timeout">{job.timeout_seconds}s per attempt</Field>
          <Field label="Backoff">
            base {job.backoff_base_seconds}s × {job.backoff_factor}, cap{" "}
            {job.backoff_max_seconds}s
          </Field>
          <Field label="Idempotency key">
            {job.idempotency_key ? (
              <span className="font-mono text-xs">{job.idempotency_key}</span>
            ) : (
              "—"
            )}
          </Field>
          <Field label="Worker">
            {job.locked_by ? (
              <span className="font-mono text-xs">{shortId(job.locked_by)}</span>
            ) : (
              "—"
            )}
          </Field>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Payload</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-md bg-background p-3 font-mono text-xs">
              {JSON.stringify(job.payload, null, 2)}
            </pre>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{job.result ? "Result" : "Last error"}</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-background p-3 font-mono text-xs">
              {job.result
                ? JSON.stringify(job.result, null, 2)
                : (job.last_error ?? "—")}
            </pre>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Attempt history</CardTitle>
        </CardHeader>
        <Table>
          <THead>
            <TR>
              <TH>#</TH>
              <TH>Status</TH>
              <TH>Worker</TH>
              <TH>Started</TH>
              <TH>Duration</TH>
              <TH>Error</TH>
            </TR>
          </THead>
          <TBody>
            {attempts.map((attempt) => (
              <TR key={attempt.id}>
                <TD className="tabular-nums">{attempt.attempt_number}</TD>
                <TD>
                  <StatusBadge status={attempt.status} />
                </TD>
                <TD className="font-mono text-xs">
                  {attempt.worker_id ? shortId(attempt.worker_id) : "—"}
                </TD>
                <TD className="text-xs text-secondary">
                  {timeAgo(attempt.started_at)}
                </TD>
                <TD className="text-xs tabular-nums">
                  {duration(attempt.started_at, attempt.finished_at)}
                </TD>
                <TD className="max-w-md truncate text-xs text-secondary">
                  {attempt.error ? attempt.error.split("\n").slice(-2).join(" ") : "—"}
                </TD>
              </TR>
            ))}
            {attempts.length === 0 && (
              <TR>
                <TD colSpan={6} className="py-6 text-center text-sm text-muted">
                  No attempts yet — waiting for a worker to claim this job.
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Card>
    </div>
  );
}
