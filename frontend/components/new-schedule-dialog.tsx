"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { schedulesApi } from "@/lib/api";

const TASKS = [
  { name: "demo.echo", payload: '{\n  "message": "cron tick"\n}' },
  { name: "demo.sleep", payload: '{\n  "seconds": 5\n}' },
  { name: "demo.flaky", payload: '{\n  "failure_rate": 0.5\n}' },
];

const CRON_PRESETS = [
  { label: "every minute", expr: "* * * * *" },
  { label: "every 5 minutes", expr: "*/5 * * * *" },
  { label: "hourly", expr: "0 * * * *" },
  { label: "daily at midnight", expr: "0 0 * * *" },
];

export function NewScheduleDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [taskName, setTaskName] = useState(TASKS[0].name);
  const [payload, setPayload] = useState(TASKS[0].payload);
  const [cronExpr, setCronExpr] = useState("*/5 * * * *");
  const [queue, setQueue] = useState("default");
  const [maxAttempts, setMaxAttempts] = useState("3");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setError(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(payload || "{}");
    } catch {
      setError("payload is not valid JSON");
      return;
    }
    setSubmitting(true);
    try {
      await schedulesApi.create({
        task_name: taskName,
        payload: parsed,
        cron_expr: cronExpr,
        queue: queue || "default",
        max_attempts: Number(maxAttempts) || 3,
      });
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="New recurring schedule">
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="stask">Task</Label>
          <Select
            id="stask"
            className="w-full"
            value={taskName}
            onChange={(e) => {
              setTaskName(e.target.value);
              const preset = TASKS.find((t) => t.name === e.target.value);
              if (preset) setPayload(preset.payload);
            }}
          >
            {TASKS.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </Select>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="cron">Cron expression (UTC)</Label>
            <Input
              id="cron"
              className="font-mono"
              value={cronExpr}
              onChange={(e) => setCronExpr(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cron-preset">Preset</Label>
            <Select
              id="cron-preset"
              className="w-full"
              value=""
              onChange={(e) => e.target.value && setCronExpr(e.target.value)}
            >
              <option value="">custom…</option>
              {CRON_PRESETS.map((p) => (
                <option key={p.expr} value={p.expr}>
                  {p.label} ({p.expr})
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="squeue">Queue</Label>
            <Input
              id="squeue"
              value={queue}
              onChange={(e) => setQueue(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sattempts">Max attempts</Label>
            <Input
              id="sattempts"
              type="number"
              min={1}
              max={20}
              value={maxAttempts}
              onChange={(e) => setMaxAttempts(e.target.value)}
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="spayload">Payload (JSON)</Label>
          <Textarea
            id="spayload"
            rows={4}
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
          />
        </div>

        {error && (
          <p className="rounded-md bg-[var(--wash-failed)] px-3 py-2 text-xs text-foreground">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? "Creating…" : "Create schedule"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
