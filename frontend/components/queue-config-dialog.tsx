"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { QueueInfo, queuesApi, RetryStrategy } from "@/lib/api";

export function QueueConfigDialog({
  queue,
  onClose,
  onSaved,
}: {
  queue: QueueInfo | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [maxConcurrency, setMaxConcurrency] = useState("");
  const [shardKey, setShardKey] = useState("0");
  const [strategy, setStrategy] = useState<RetryStrategy>("exponential");
  const [maxAttempts, setMaxAttempts] = useState("3");
  const [backoffBase, setBackoffBase] = useState("5");
  const [timeoutSeconds, setTimeoutSeconds] = useState("300");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!queue) return;
    setMaxConcurrency(queue.max_concurrency?.toString() ?? "");
    setShardKey(String(queue.shard_key));
    setStrategy(queue.default_backoff_strategy);
    setMaxAttempts(String(queue.default_max_attempts));
    setBackoffBase(String(queue.default_backoff_base_seconds));
    setTimeoutSeconds(String(queue.default_timeout_seconds));
    setError(null);
  }, [queue]);

  if (!queue) return null;

  const submit = async () => {
    setError(null);
    setSaving(true);
    try {
      await queuesApi.update(queue.id, {
        max_concurrency: maxConcurrency === "" ? null : Number(maxConcurrency),
        shard_key: Number(shardKey) || 0,
        default_backoff_strategy: strategy,
        default_max_attempts: Number(maxAttempts) || 3,
        default_backoff_base_seconds: Number(backoffBase) || 5,
        default_timeout_seconds: Number(timeoutSeconds) || 300,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onClose={onClose} title={`Configure queue "${queue.name}"`}>
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="cap">Concurrency cap (fleet-wide)</Label>
          <Input
            id="cap"
            type="number"
            min={1}
            max={10000}
            placeholder="unlimited"
            value={maxConcurrency}
            onChange={(e) => setMaxConcurrency(e.target.value)}
          />
          <p className="text-xs text-muted">
            Leave empty for unlimited. Applies at claim time; running jobs are
            never interrupted.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="shard">Shard key</Label>
          <Input
            id="shard"
            type="number"
            min={0}
            max={255}
            value={shardKey}
            onChange={(e) => setShardKey(e.target.value)}
          />
          <p className="text-xs text-muted">
            Workers subscribe to a shard via WORKER_SHARD env. 0 = general pool.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="strategy">Default retry strategy</Label>
            <Select
              id="strategy"
              className="w-full"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as RetryStrategy)}
            >
              <option value="exponential">exponential</option>
              <option value="linear">linear</option>
              <option value="fixed">fixed</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="qattempts">Default max attempts</Label>
            <Input
              id="qattempts"
              type="number"
              min={1}
              max={20}
              value={maxAttempts}
              onChange={(e) => setMaxAttempts(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="qbase">Default backoff base (s)</Label>
            <Input
              id="qbase"
              type="number"
              min={0}
              max={3600}
              value={backoffBase}
              onChange={(e) => setBackoffBase(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="qtimeout">Default timeout (s)</Label>
            <Input
              id="qtimeout"
              type="number"
              min={1}
              max={86400}
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(e.target.value)}
            />
          </div>
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
          <Button onClick={submit} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
