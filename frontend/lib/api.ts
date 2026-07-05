const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "chronos_token";

export type JobStatus = "pending" | "running" | "succeeded" | "cancelled" | "dead";
export type RetryStrategy = "fixed" | "linear" | "exponential";

export interface Job {
  id: string;
  owner_id: string;
  queue_id: string;
  queue: string;
  task_name: string;
  batch_id: string | null;
  workflow_id: string | null;
  payload: Record<string, unknown>;
  status: JobStatus;
  priority: number;
  run_at: string;
  idempotency_key: string | null;
  max_attempts: number;
  attempt_count: number;
  backoff_strategy: RetryStrategy;
  timeout_seconds: number;
  backoff_base_seconds: number;
  backoff_factor: number;
  backoff_max_seconds: number;
  locked_by: string | null;
  lease_expires_at: string | null;
  last_error: string | null;
  ai_summary: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobDependency {
  id: string;
  job_id: string;
  depends_on_job_id: string;
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
  backoff_strategy?: RetryStrategy;
  backoff_base_seconds?: number;
  idempotency_key?: string;
  depends_on?: string[];
}

export interface ProjectInfo {
  id: string;
  org_id: string;
  name: string;
  created_at: string;
}

export interface QueueInfo {
  id: string;
  project_id: string;
  name: string;
  paused: boolean;
  shard_key: number;
  max_concurrency: number | null;
  default_priority: number;
  default_max_attempts: number;
  default_backoff_strategy: RetryStrategy;
  default_backoff_base_seconds: number;
  default_backoff_factor: number;
  default_backoff_max_seconds: number;
  default_timeout_seconds: number;
  created_at: string;
  updated_at: string;
  counts_by_status: Record<JobStatus, number>;
}

export interface QueueUpdateInput {
  paused?: boolean;
  shard_key?: number;
  max_concurrency?: number | null;
  default_priority?: number;
  default_max_attempts?: number;
  default_backoff_strategy?: RetryStrategy;
  default_backoff_base_seconds?: number;
  default_backoff_factor?: number;
  default_backoff_max_seconds?: number;
  default_timeout_seconds?: number;
}

export interface ScheduleInfo {
  id: string;
  owner_id: string;
  queue_id: string;
  queue: string;
  task_name: string;
  payload: Record<string, unknown>;
  cron_expr: string;
  paused: boolean;
  priority: number;
  max_attempts: number;
  timeout_seconds: number;
  backoff_strategy: RetryStrategy;
  backoff_base_seconds: number;
  backoff_factor: number;
  backoff_max_seconds: number;
  next_run_at: string;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleCreateInput {
  task_name: string;
  payload: Record<string, unknown>;
  cron_expr: string;
  queue?: string;
  priority?: number;
  max_attempts?: number;
  timeout_seconds?: number;
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
  if (res.status === 204) return undefined as T;
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
  dependencies: (id: string) => api<JobDependency[]>(`/api/v1/jobs/${id}/dependencies`),
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

export const projectsApi = {
  list: () => api<ProjectInfo[]>("/api/v1/projects"),
};

export const queuesApi = {
  list: () => api<QueueInfo[]>("/api/v1/queues"),
  pause: (id: string) => api<QueueInfo>(`/api/v1/queues/${id}/pause`, { method: "POST" }),
  resume: (id: string) =>
    api<QueueInfo>(`/api/v1/queues/${id}/resume`, { method: "POST" }),
  update: (id: string, input: QueueUpdateInput) =>
    api<QueueInfo>(`/api/v1/queues/${id}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
};

export const schedulesApi = {
  list: (limit = 50, offset = 0) =>
    api<PageOf<ScheduleInfo>>(`/api/v1/schedules?limit=${limit}&offset=${offset}`),
  create: (input: ScheduleCreateInput) =>
    api<ScheduleInfo>("/api/v1/schedules", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  setPaused: (id: string, paused: boolean) =>
    api<ScheduleInfo>(`/api/v1/schedules/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ paused }),
    }),
  remove: (id: string) =>
    api<void>(`/api/v1/schedules/${id}`, { method: "DELETE" }),
};

export const statsApi = {
  overview: () => api<StatsOverview>("/api/v1/stats/overview"),
};

export type UserRole = "owner" | "admin" | "member" | "viewer";

export interface UserInfo {
  id: string;
  email: string;
  role: UserRole;
  created_at: string;
}

export const authApi = {
  me: () => api<UserInfo>("/api/v1/auth/me"),
};

export const usersApi = {
  list: () => api<UserInfo[]>("/api/v1/users"),
  updateRole: (id: string, role: UserRole) =>
    api<UserInfo>(`/api/v1/users/${id}/role`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
};
