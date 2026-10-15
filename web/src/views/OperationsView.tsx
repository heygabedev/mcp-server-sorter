import { useCallback, useEffect, useRef, useState } from 'react';
import { api, waitForJob } from '../api';
import type { Job } from '../api';
import PageTitle from './PageTitle';

interface Operations {
  mode: string;
  jobs: Job[];
  events: { id: number; at: number; kind: string; code: string }[];
  alerts: string[];
  traces: {
    trace_id: string;
    span_id?: string;
    name: string;
    status: number;
    attributes?: {
      'http.request.method'?: string;
      'http.route'?: string;
      'job.outcome'?: string;
      'job.id'?: string;
    };
    links?: { trace_id: string; span_id: string }[];
  }[];
  telemetry: string;
  worker_enabled: boolean;
}

export default function OperationsView({
  onCatalogChanged,
}: {
  onCatalogChanged: () => Promise<void>;
}) {
  const [status, setStatus] = useState<Operations | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const retryKeys = useRef(new Map<string, string>());
  const requestNumber = useRef(0);
  const reload = useCallback(async () => {
    const sequence = ++requestNumber.current;
    try {
      const result = await api<Operations>('/operations');
      if (sequence === requestNumber.current) setStatus(result);
    } catch (cause) {
      if (sequence === requestNumber.current)
        setError(cause instanceof Error ? cause.message : 'Unable to load operations');
    }
  }, []);
  useEffect(() => {
    void reload();
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
  }, [reload]);
  async function refresh() {
    setBusy(true);
    setError('');
    try {
      const job = await api<Job>('/jobs', {
        kind: 'catalog-refresh',
        idempotency_key: crypto.randomUUID(),
      });
      await reload();
      await waitForJob(job);
      await reload();
      await onCatalogChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Refresh failed');
    } finally {
      setBusy(false);
    }
  }

  async function retry(job: Job) {
    setBusy(true);
    setError('');
    setNotice('');
    const key = retryKeys.current.get(job.id) ?? crypto.randomUUID();
    retryKeys.current.set(job.id, key);
    try {
      const retried = await api<Job>(`/jobs/${job.id}/retry`, { idempotency_key: key });
      await reload();
      await waitForJob(retried);
      await reload();
      await onCatalogChanged();
      retryKeys.current.delete(job.id);
      setNotice('Retry completed. Original inputs were preserved.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Retry failed');
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="LOCAL OPERATIONS"
        title="See what happened."
        description="Inspect durable jobs, recent fallback events, and local request traces. Demo failures are simulated; job state and recovery are real."
      />
      {error && (
        <p role="alert" className="alert error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="alert">
          {notice}
        </p>
      )}
      {status && (
        <>
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>Worker and telemetry</h2>
                <p>
                  {status.worker_enabled
                    ? 'Background worker enabled'
                    : 'Background worker disabled; use the CLI worker'}
                </p>
                <p className="caption">{status.telemetry}</p>
              </div>
              <button className="primary" disabled={busy} onClick={() => void refresh()}>
                {busy ? 'Refreshing catalog…' : 'Refresh catalog'}
              </button>
            </div>
            {status.mode === 'demo' && (
              <p className="caption">
                Refresh publishes a second fixture version with an illustrative GitHub version
                change. No external calls.
              </p>
            )}
            {status.alerts.map((alert) => (
              <p className="alert" role="status" key={alert}>
                {alert}
              </p>
            ))}
          </section>
          <section className="panel">
            <h2>Durable jobs</h2>
            {status.jobs.length === 0 ? (
              <p>No jobs yet. Run an evaluation or refresh the catalog.</p>
            ) : (
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Durable jobs">
                <table>
                  <thead>
                    <tr>
                      <th>Job</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Failure category</th>
                      <th>Attempts</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.jobs.map((job) => (
                      <tr key={job.id}>
                        <td>
                          <code>{job.id.slice(0, 12)}</code>
                          {job.parent_job_id && (
                            <p className="caption">
                              Retry of <code>{job.parent_job_id.slice(0, 12)}</code>
                            </p>
                          )}
                        </td>
                        <td>{jobKind(job.payload)}</td>
                        <td>{job.status}</td>
                        <td>{job.error_code?.replaceAll('-', ' ') ?? '—'}</td>
                        <td>
                          <details>
                            <summary>
                              Attempts for {job.id.slice(0, 12)} ({job.attempts} / 3)
                            </summary>
                            {job.attempts > (job.attempt_history?.length ?? 0) && (
                              <p>Earlier attempts were not recorded.</p>
                            )}
                            <ol className="attempt-history">
                              {(job.attempt_history ?? []).map((attempt) => (
                                <li key={attempt.attempt}>
                                  <strong>
                                    Attempt {attempt.attempt}:{' '}
                                    {attempt.outcome.replaceAll('-', ' ')}
                                  </strong>
                                  <p>
                                    Started {new Date(attempt.started_at * 1000).toLocaleString()}
                                    {attempt.finished_at !== null
                                      ? ` · finished ${new Date(attempt.finished_at * 1000).toLocaleString()}`
                                      : ' · in progress'}
                                  </p>
                                  {attempt.error_code && (
                                    <p>{attempt.error_code.replaceAll('-', ' ')}</p>
                                  )}
                                  <p>
                                    Trace: <code>{attempt.trace_id ?? 'Not recorded'}</code>
                                  </p>
                                </li>
                              ))}
                            </ol>
                            {job.request_trace_id && (
                              <p>
                                Request trace: <code>{job.request_trace_id}</code>
                              </p>
                            )}
                          </details>
                        </td>
                        <td>
                          {job.status === 'failed' && (
                            <button
                              className="secondary"
                              disabled={busy}
                              aria-label={`Retry job ${job.id.slice(0, 12)}`}
                              onClick={() => void retry(job)}
                            >
                              Retry job
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section className="panel">
            <h2>Recent events</h2>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Recent events">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Type</th>
                    <th>Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {status.events.slice(0, 20).map((event) => (
                    <tr key={event.id}>
                      <td>{new Date(event.at * 1000).toLocaleTimeString()}</td>
                      <td>{event.kind}</td>
                      <td>{event.code}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="panel">
            <h2>Request traces</h2>
            <p className="caption">
              Latest 100 spans in memory. Queries, credentials, and model content are excluded.
            </p>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Request traces">
              <table>
                <thead>
                  <tr>
                    <th>Trace</th>
                    <th>Operation</th>
                    <th>Outcome</th>
                    <th>Linked request</th>
                  </tr>
                </thead>
                <tbody>
                  {status.traces
                    .slice(-10)
                    .reverse()
                    .map((trace) => (
                      <tr key={`${trace.trace_id}:${trace.span_id ?? trace.name}`}>
                        <td>
                          <code>{trace.trace_id.slice(0, 16)}</code>
                        </td>
                        <td>
                          {trace.name}
                          <p className="caption">
                            {trace.attributes?.['http.request.method']}{' '}
                            {trace.attributes?.['http.route'] ??
                              trace.attributes?.['job.id']?.slice(0, 12)}
                          </p>
                        </td>
                        <td>{trace.attributes?.['job.outcome'] ?? trace.status}</td>
                        <td>
                          {trace.links?.map((link) => (
                            <code key={link.span_id}>{link.trace_id.slice(0, 16)}</code>
                          )) ?? '—'}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}

function jobKind(payload: string): string {
  try {
    const kind = JSON.parse(payload)?.kind;
    return kind === 'evaluation' || kind === 'catalog-refresh' ? kind : 'Unknown job';
  } catch {
    return 'Unknown job';
  }
}
