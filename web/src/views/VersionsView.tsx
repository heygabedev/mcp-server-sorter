import { useEffect, useState } from 'react';
import { api, download } from '../api';
import PageTitle from './PageTitle';

interface Versions {
  active_catalog: string;
  active_configuration: string;
  snapshots: string[];
  configurations: Record<string, { name: string; version: string; name_weight: number }>;
}

export default function VersionsView() {
  const [versions, setVersions] = useState<Versions | null>(null);
  const [snapshot, setSnapshot] = useState('');
  const [configuration, setConfiguration] = useState('');
  const [diff, setDiff] = useState<Record<string, string[]> | null>(null);
  const [backups, setBackups] = useState<string[]>([]);
  const [selectedBackup, setSelectedBackup] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function reload() {
    const [current, saved] = await Promise.all([
      api<Versions>('/versions'),
      api<string[]>('/backups'),
    ]);
    setVersions(current);
    setSnapshot(current.active_catalog);
    setConfiguration(current.active_configuration);
    setBackups(saved);
  }
  useEffect(() => {
    void reload().catch((cause) => setError(String(cause)));
  }, []);

  async function action(task: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await task();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Operation failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageTitle
        eyebrow="REPRODUCIBLE BY DESIGN"
        title="A catalog with a memory."
        description="Inspect differences, activate earlier catalog and ranking versions, and verify recovery backups."
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
      {versions && (
        <>
          <section className="panel">
            <h2>Active versions</h2>
            <p>
              Catalog <code className="hash">{versions.active_catalog}</code>
            </p>
            <p>
              Configuration <code className="hash">{versions.active_configuration}</code>
            </p>
            <p className="caption">
              Each catalog retains its own search index. Saved collections keep their pinned records
              and evidence.
            </p>
          </section>
          <section className="panel">
            <h2>Compare and activate versions</h2>
            <div className="inline-form">
              <label>
                Catalog snapshot
                <select
                  aria-label="Catalog snapshot"
                  value={snapshot}
                  onChange={(event) => setSnapshot(event.target.value)}
                >
                  {versions.snapshots.map((id) => (
                    <option key={id} value={id}>
                      {id.slice(0, 20)}
                      {id === versions.active_catalog ? ' · active' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Ranking configuration
                <select
                  aria-label="Ranking configuration"
                  value={configuration}
                  onChange={(event) => setConfiguration(event.target.value)}
                >
                  {Object.entries(versions.configurations).map(([id, value]) => (
                    <option key={id} value={id}>
                      {value.name} · {value.version}
                      {id === versions.active_configuration ? ' · active' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void action(async () =>
                    setDiff(
                      await api(
                        `/versions/diff?before=${snapshot}&after=${versions.active_catalog}`,
                      ),
                    ),
                  )
                }
              >
                Compare with active
              </button>
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await api('/versions/activate', { snapshot, configuration });
                    await reload();
                    setNotice(
                      'Catalog and configuration activated together. Collections preserved.',
                    );
                  })
                }
              >
                Activate selected versions
              </button>
            </div>
            {diff && (
              <div className="metric-grid">
                {Object.entries(diff).map(([key, values]) => (
                  <div className="stat" key={key}>
                    <strong>{values.length}</strong>
                    <span>{key}</span>
                    <p>{values.join(', ')}</p>
                  </div>
                ))}
              </div>
            )}
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                void action(async () => {
                  await api('/configurations', { name: 'Titles first', name_weight: 8 });
                  await reload();
                  setNotice(
                    'Alternative ranking configuration saved. Select it above to activate it.',
                  );
                })
              }
            >
              Add title-focused configuration
            </button>
          </section>
          <section className="panel">
            <h2>Backups and recovery</h2>
            <p>
              Backups include the state database, catalog indexes, ranking configurations, and JSON
              evaluation reports. Checksums are verified before a backup becomes available.
            </p>
            <div className="inline-form">
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    const result = await api<{ id: string }>('/backups', {});
                    await reload();
                    setSelectedBackup(result.id);
                    setNotice('Backup created and verified.');
                  })
                }
              >
                Create verified backup
              </button>
              <select
                aria-label="Saved backup"
                value={selectedBackup}
                onChange={(event) => setSelectedBackup(event.target.value)}
              >
                <option value="">Choose a backup</option>
                {backups.map((id) => (
                  <option key={id} value={id}>
                    {id.slice(0, 16)}
                  </option>
                ))}
              </select>
              <button
                className="secondary"
                disabled={busy || !selectedBackup}
                onClick={() =>
                  void action(async () => {
                    const manifest = await api(`/backups/${selectedBackup}`);
                    download(`backup-${selectedBackup}-manifest.json`, manifest);
                    setNotice('Backup checksums and schema verified. Manifest downloaded.');
                  })
                }
              >
                Verify and export manifest
              </button>
            </div>
            {selectedBackup && (
              <div className="recovery-command">
                <h3>Restore to a new directory</h3>
                <p>
                  Stop the service, then run this command with your data directory set. Current
                  collections are preserved and a pre-restore backup is retained.
                </p>
                <code className="hash">
                  mcp-sorter backup restore .data/backups/{selectedBackup} .data-restored
                </code>
                <p className="caption">
                  The command uses the default .data location. Use your configured path if
                  different. Start the restored copy with SORTER_DATA_DIR set to .data-restored.
                </p>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
