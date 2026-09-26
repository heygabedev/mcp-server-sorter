import type { components } from './schema';

export type Server = components['schemas']['ServerRecord'];
export type Ranking = components['schemas']['Ranking'];
export type Selection = components['schemas']['Selection'];
export type Report = components['schemas']['EvaluationReport'];
export type Profile = NonNullable<components['schemas']['RankRequest']['profile']>;

export interface Job {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  attempts: number;
  payload: string;
  result: string | null;
  error_code: string | null;
}

export async function waitForJob(job: Job): Promise<Job> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const current = await api<Job>(`/jobs/${job.id}`);
    if (current.status === 'completed') return current;
    if (current.status === 'failed')
      throw new Error('The job exhausted its retries. See Operations.');
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('The job is still queued or running. Check its progress in Operations.');
}

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === 'string' ? error.detail : `Request failed (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

export function download(name: string, data: unknown): void {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
