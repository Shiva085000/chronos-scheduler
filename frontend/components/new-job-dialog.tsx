"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { jobsApi } from "@/lib/api";

const TASKS = [
  { name: "demo.echo", payload: '{\n  "message": "hello"\n}' },
  { name: "demo.sleep", payload: '{\n  "seconds": 10\n}' },
  { name: "demo.fail_until", payload: '{\n  "succeed_on_attempt": 3\n}' },
  { name: "demo.flaky", payload: '{\n  "failure_rate": 0.5\n}' },
  { name: "demo.always_fail", payload: "{}" },
];

export function NewJobDialog({
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
  const [priority, setPriority] = useState("0");
  const [maxAttempts, setMaxAttempts] = useState("3");
  const [timeoutSeconds, setTimeoutSeconds] = useState("300");
  const [delaySeconds, setDelaySeconds] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
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
      await jobsApi.create({
        task_name: taskName,
        payload: parsed,
        priority: Number(priority) || 0,
        max_attempts: Number(maxAttempts) || 3,
        timeout_seconds: Number(timeoutSeconds) || 300,
        ...(delaySeconds
          ? {
              run_at: new Date(
                Date.now() + Number(delaySeconds) * 1000,
              ).toISOString(),
            }
          : {}),
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      });
      onCreated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="Enqueue job">
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="task">Task</Label>
          <Select
            id="task"
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

        <div className="space-y-1.5">
          <Label htmlFor="payload">Payload (JSON)</Label>
          <Textarea
            id="payload"
            rows={4}
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="priority">Priority</Label>
            <Input
              id="priority"
              type="number"
              min={-100}
              max={100}
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="attempts">Max attempts</Label>
            <Input
              id="attempts"
              type="number"
              min={1}
              max={20}
              value={maxAttempts}
              onChange={(e) => setMaxAttempts(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="delay">Delay (seconds)</Label>
            <Input
              id="delay"
              type="number"
              min={0}
              placeholder="run now"
              value={delaySeconds}
              onChange={(e) => setDelaySeconds(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="timeout">Timeout (seconds)</Label>
            <Input
              id="timeout"
              type="number"
              min={1}
              max={86400}
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(e.target.value)}
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="idem">Idempotency key (optional)</Label>
          <Input
            id="idem"
            placeholder="e.g. order-1234-welcome-email"
            value={idempotencyKey}
            onChange={(e) => setIdempotencyKey(e.target.value)}
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
            {submitting ? "Enqueuing…" : "Enqueue"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
