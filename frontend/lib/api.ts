const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "chronos_token";

export type JobStatus = "pending" | "running" | "succeeded" | "cancelled" | "dead";

export interface Job {
  id: string;
  owner_id: string;
  queue: string;
  task_name: string;
  payload: Record<string, unknown>;
  status: JobStatus;
  priority: number;
  run_at: string;
  idempotency_key: string | null;
  max_attempts: number;
  attempt_count: number;
  timeout_seconds: number;
  backoff_base_seconds: number;
  backoff_factor: number;
  backoff_max_seconds: number;
  locked_by: string | null;
  lease_expires_at: string | null;
  last_error: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Attempt {
  id: string;
  job_id: string;
  worker_id: string | null;
  attempt_number: number;
  status: "running" | "succeeded" | "failed" | "lost" | "aborted";
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface WorkerInfo {
  id: string;
  name: string;
  status: "online" | "draining" | "offline";
  concurrency: number;
  last_heartbeat_at: string;
  started_at: string;
  stopped_at: string | null;
}

export interface ThroughputPoint {
  minute: string;
  succeeded: number;
  failed: number;
}

export interface StatsOverview {
  counts_by_status: Record<string, number>;
  ready_now: number;
  scheduled_later: number;
  workers_online: number;
  dlq_size: number;
  throughput_last_hour: ThroughputPoint[];
  generated_at: string;
}

export interface PageOf<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface JobCreateInput {
  task_name: string;
  payload: Record<string, unknown>;
  queue?: string;
  priority?: number;
  run_at?: string;
  max_attempts?: number;
  timeout_seconds?: number;
  backoff_base_seconds?: number;
  idempotency_key?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return res.statusText;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, { ...init, headers });

  if (res.status === 401 && typeof window !== "undefined") {
    clearToken();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
  }
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function login(email: string, password: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: email, password }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  const body = await res.json();
  setToken(body.access_token);
}

export async function register(email: string, password: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

export const jobsApi = {
  list: (params: { status?: string; queue?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.queue) q.set("queue", params.queue);
    q.set("limit", String(params.limit ?? 50));
    q.set("offset", String(params.offset ?? 0));
    return api<PageOf<Job>>(`/api/v1/jobs?${q}`);
  },
  get: (id: string) => api<Job>(`/api/v1/jobs/${id}`),
  attempts: (id: string) => api<Attempt[]>(`/api/v1/jobs/${id}/attempts`),
  create: (input: JobCreateInput) =>
    api<Job>("/api/v1/jobs", { method: "POST", body: JSON.stringify(input) }),
  cancel: (id: string) => api<Job>(`/api/v1/jobs/${id}/cancel`, { method: "POST" }),
  requeue: (id: string) => api<Job>(`/api/v1/jobs/${id}/requeue`, { method: "POST" }),
};

export const dlqApi = {
  list: (limit = 50, offset = 0) =>
    api<PageOf<Job>>(`/api/v1/dlq?limit=${limit}&offset=${offset}`),
  requeue: (id: string) => api<Job>(`/api/v1/dlq/${id}/requeue`, { method: "POST" }),
};

export const workersApi = {
  list: () => api<WorkerInfo[]>("/api/v1/workers"),
};

export const statsApi = {
  overview: () => api<StatsOverview>("/api/v1/stats/overview"),
};
