import { useCallback, useEffect, useState } from 'react';
import { api, waitForJob } from '../api';
import type { Job } from '../api';
import PageTitle from './PageTitle';

interface Operations {
  mode: string;
  jobs: Job[];
  events: { id: number; at: number; kind: string; code: string }[];
  alerts: string[];
  traces: { trace_id: string; name: string; status: number }[];
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
  const reload = useCallback(async () => {
    try {
      setStatus(await api<Operations>('/operations'));
    } catch (cause) {
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
                      <th>Attempts</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.jobs.map((job) => (
                      <tr key={job.id}>
                        <td>
                          <code>{job.id.slice(0, 12)}</code>
                        </td>
                        <td>{JSON.parse(job.payload).kind}</td>
                        <td>{job.status}</td>
                        <td>{job.attempts} / 3</td>
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
                    <th>HTTP status</th>
                  </tr>
                </thead>
                <tbody>
                  {status.traces
                    .slice(-10)
                    .reverse()
                    .map((trace) => (
                      <tr key={trace.trace_id}>
                        <td>
                          <code>{trace.trace_id.slice(0, 16)}</code>
                        </td>
                        <td>{trace.name}</td>
                        <td>{trace.status}</td>
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
