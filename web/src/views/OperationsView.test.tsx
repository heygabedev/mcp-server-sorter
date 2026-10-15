import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import OperationsView from './OperationsView';

const failed = {
  id: 'a'.repeat(32),
  status: 'failed',
  attempts: 3,
  payload: '{"kind":"evaluation"}',
  error_code: 'timeout',
  result: null,
  parent_job_id: null,
  attempt_history: [
    {
      attempt: 3,
      started_at: 100,
      finished_at: 101,
      outcome: 'failed',
      error_code: 'timeout',
      trace_id: 'c'.repeat(32),
    },
  ],
};
const status = {
  mode: 'demo',
  jobs: [failed],
  events: [],
  traces: [],
  alerts: [],
  telemetry: 'Local only',
  worker_enabled: true,
};

test('shows attempts and reuses the retry key after a failed HTTP response', async () => {
  const keys: string[] = [];
  const refresh = vi.fn();
  const retried = { ...failed, id: 'b'.repeat(32), status: 'completed', parent_job_id: failed.id };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path.endsWith('/retry')) {
        keys.push(JSON.parse(String(init?.body)).idempotency_key);
        return keys.length === 1
          ? Response.json({ detail: 'Temporary connection failure' }, { status: 503 })
          : Response.json(retried);
      }
      if (path.endsWith('/operations')) return Response.json(status);
      return Response.json(retried);
    }),
  );
  render(<OperationsView onCatalogChanged={refresh} />);
  const button = await screen.findByRole('button', { name: `Retry job ${failed.id.slice(0, 12)}` });
  fireEvent.click(screen.getByText(`Attempts for ${failed.id.slice(0, 12)} (3 / 3)`));
  expect(screen.getByText('Attempt 3: failed')).toBeVisible();
  expect(screen.getByText('Earlier attempts were not recorded.')).toBeVisible();
  fireEvent.click(button);
  expect(await screen.findByRole('alert')).toHaveTextContent('Temporary connection failure');
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(
    await screen.findByText('Retry completed. Original inputs were preserved.'),
  ).toBeInTheDocument();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(refresh).toHaveBeenCalledOnce();
});

test('keeps diagnostics usable when a stored job payload is malformed', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ ...status, jobs: [{ ...failed, payload: 'not json' }] })),
  );
  render(<OperationsView onCatalogChanged={vi.fn()} />);
  expect(await screen.findByText('Unknown job')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: `Retry job ${failed.id.slice(0, 12)}` })).toBeEnabled();
});
