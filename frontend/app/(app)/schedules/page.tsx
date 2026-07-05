"use client";

import { useState } from "react";
import { CalendarPlus, Pause, Play, Trash2 } from "lucide-react";

import { NewScheduleDialog } from "@/components/new-schedule-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ScheduleInfo, schedulesApi } from "@/lib/api";
import { shortId, timeAgo } from "@/lib/format";
import { usePolling } from "@/hooks/use-polling";
import { cn } from "@/lib/utils";

export default function SchedulesPage() {
  const { data, refresh } = usePolling(() => schedulesApi.list(), 5000);
  const [creating, setCreating] = useState(false);

  const togglePause = async (s: ScheduleInfo) => {
    try {
      await schedulesApi.setPaused(s.id, !s.paused);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  const remove = async (s: ScheduleInfo) => {
    if (!confirm(`Delete schedule ${s.cron_expr} → ${s.task_name}?`)) return;
    try {
      await schedulesApi.remove(s.id);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold">Schedules</h1>
          <p className="text-sm text-secondary">
            Recurring (cron) schedules, evaluated in UTC. Each firing
            materializes an ordinary job; missed ticks collapse into a single
            run.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <CalendarPlus className="h-4 w-4" aria-hidden /> New schedule
        </Button>
      </div>

      <Card>
        <Table>
          <THead>
            <TR>
              <TH>Schedule</TH>
              <TH>Cron</TH>
              <TH>Task</TH>
              <TH>Queue</TH>
              <TH>State</TH>
              <TH>Next run (UTC)</TH>
              <TH>Last run</TH>
              <TH className="text-right">Actions</TH>
            </TR>
          </THead>
          <TBody>
            {data?.items.map((s) => (
              <TR key={s.id}>
                <TD className="font-mono text-xs">{shortId(s.id)}</TD>
                <TD className="font-mono text-xs">{s.cron_expr}</TD>
                <TD className="font-mono text-xs">{s.task_name}</TD>
                <TD className="font-mono text-xs">{s.queue}</TD>
                <TD>
                  <span
                    className={cn(
                      "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                      s.paused
                        ? "bg-[var(--wash-failed)]"
                        : "bg-[var(--wash-succeeded)]",
                    )}
                  >
                    {s.paused ? "paused" : "active"}
                  </span>
                </TD>
                <TD className="text-xs text-secondary tabular-nums">
                  {new Date(s.next_run_at).toISOString().replace("T", " ").slice(0, 19)}
                </TD>
                <TD className="text-xs text-secondary">{timeAgo(s.last_run_at)}</TD>
                <TD className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="sm" onClick={() => togglePause(s)}>
                      {s.paused ? (
                        <>
                          <Play className="h-3.5 w-3.5" aria-hidden /> Resume
                        </>
                      ) : (
                        <>
                          <Pause className="h-3.5 w-3.5" aria-hidden /> Pause
                        </>
                      )}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => remove(s)}>
                      <Trash2 className="h-3.5 w-3.5" aria-hidden /> Delete
                    </Button>
                  </div>
                </TD>
              </TR>
            ))}
            {data && data.items.length === 0 && (
              <TR>
                <TD colSpan={8} className="py-8 text-center text-sm text-muted">
                  No schedules yet.
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Card>

      <NewScheduleDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          refresh();
        }}
      />
    </div>
  );
}
