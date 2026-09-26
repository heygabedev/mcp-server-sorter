import { profiles } from './profiles';
import PageTitle from './views/PageTitle';
import CollectionsView from './views/CollectionsView';
import EvaluationsView from './views/EvaluationsView';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import {
  ArrowRight,
  Check,
  ChevronRight,
  CircleHelp,
  Folder,
  GitCompareArrows,
  Layers3,
  Search,
  SlidersHorizontal,
  Terminal,
  X,
} from 'lucide-react';
import { api, download } from './api';
import type { Profile, Ranking, Report, Selection, Server } from './api';

type Tab = 'Discover' | 'Collections' | 'Evaluations' | 'Versions';
const categories = [
  'development',
  'knowledge',
  'communication',
  'design',
  'database',
  'files',
  'research',
  'data',
  'productivity',
  'operations',
];
const icons = {
  Discover: Search,
  Collections: Folder,
  Evaluations: SlidersHorizontal,
  Versions: Layers3,
};

export default function App() {
  const [tab, setTab] = useState<Tab>('Discover');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [deployment, setDeployment] = useState('');
  const [profile, setProfile] = useState<Profile>('baseline');
  const [ranking, setRanking] = useState<Ranking | null>(null);
  const [selected, setSelected] = useState<Server[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [detail, setDetail] = useState<Server | null>(null);
  const [compare, setCompare] = useState(false);
  const [collectionName, setCollectionName] = useState('My toolkit');
  const [collections, setCollections] = useState<Selection[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [versions, setVersions] = useState<{ active_catalog: string; snapshots: string[] } | null>(
    null,
  );
  const [previous, setPrevious] = useState('');
  const [diff, setDiff] = useState<Record<string, string[]> | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const requestNumber = useRef(0);

  async function search(value = query) {
    const sequence = ++requestNumber.current;
    setBusy(true);
    setError('');
    try {
      const result = await api<Ranking>('/rankings', {
        query: value,
        filters: { category: category || null, deployment: deployment || null },
        profile,
        limit: 50,
      });
      if (sequence === requestNumber.current) {
        if (ranking && ranking.snapshot !== result.snapshot) setSelected([]);
        setRanking(result);
      }
    } catch (cause) {
      if (sequence === requestNumber.current) setError(message(cause));
    } finally {
      if (sequence === requestNumber.current) setBusy(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    api<Ranking>('/servers', undefined, controller.signal)
      .then(setRanking)
      .catch((cause) => {
        if (!controller.signal.aborted) setError(message(cause));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (detail || compare) dialog.current?.showModal();
    else dialog.current?.close();
  }, [detail, compare]);

  async function navigate(next: Tab) {
    setTab(next);
    setError('');
    setNotice('');
    try {
      if (next === 'Collections') setCollections(await api<Selection[]>('/collections'));
      if (next === 'Evaluations') setReports(await api<Report[]>('/evaluations'));
      if (next === 'Versions') setVersions(await api('/versions'));
    } catch (cause) {
      setError(message(cause));
    }
  }

  function toggle(server: Server) {
    setSelected((current) =>
      current.some((item) => item.id === server.id)
        ? current.filter((item) => item.id !== server.id)
        : current.length < 4
          ? [...current, server]
          : current,
    );
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api<Selection>('/collections', {
        name: collectionName,
        ids: selected.map((s) => s.id),
        snapshot: ranking?.snapshot,
      });
      setNotice(`Saved “${collectionName}” with ${selected.length} pinned server versions.`);
      setCompare(false);
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  async function runEvaluation() {
    setBusy(true);
    setError('');
    try {
      const result = await api<Report>('/evaluations', { profile });
      setReport(result);
      setReports((current) => [result, ...current]);
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  async function exportCollection(selection: Selection) {
    try {
      download(`selection-${selection.id}.json`, await api(`/collections/${selection.id}/export`));
    } catch (cause) {
      setError(message(cause));
    }
  }

  async function importCollection(file?: File) {
    if (!file) return;
    try {
      if (file.size > 1_000_000) throw new Error('Choose a selection file smaller than 1 MB.');
      await api('/collections/import', JSON.parse(await file.text()));
      setCollections(await api('/collections'));
      setNotice('Selection imported. Pinned evidence has been preserved.');
    } catch (cause) {
      setError(message(cause));
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            void navigate('Discover');
          }}
          aria-label="MCP Server Sorter home"
        >
          <span className="brand-mark">
            <Layers3 size={23} />
          </span>
          <span>
            mcp<span className="brand-light">sorter</span>
            <small>THE INTEGRATION WORKBENCH</small>
          </span>
        </a>
        <div className="workspace-label">
          WORKSPACE <span>01</span>
        </div>
        <nav aria-label="Main navigation">
          {(Object.keys(icons) as Tab[]).map((item) => {
            const Icon = icons[item];
            return (
              <button
                key={item}
                className={`nav-item ${tab === item ? 'active' : ''}`}
                aria-current={tab === item ? 'page' : undefined}
                onClick={() => void navigate(item)}
              >
                <Icon size={18} />
                <span>{item}</span>
                {item === 'Discover' && <span className="nav-count">30</span>}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-note">
          <Terminal size={19} />
          <strong>Built to run locally.</strong>
          <p>Your catalog, collections, and evaluations stay on this machine.</p>
          <code>mcp-sorter serve</code>
        </div>
        <div className="sidebar-footer">
          <span className="status-dot" /> Offline showcase <span>v0.1.0</span>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <span>
            Workspace <ChevronRight size={14} /> <strong>{tab}</strong>
          </span>
          <span className="demo-label">
            DEMO DATA <span className="status-dot" />
          </span>
        </header>
        <main id="main" tabIndex={-1}>
          {error && (
            <div className="alert error" role="alert">
              {error}
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError('')}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="alert" role="status">
              <Check size={17} />
              {notice}
            </div>
          )}

          {tab === 'Discover' && (
            <>
              <section className="hero">
                <div>
                  <div className="eyebrow">
                    <span /> LESS SEARCHING. BETTER CONNECTIONS.
                  </div>
                  <h1>
                    The right server.
                    <br />
                    <span>For the job at hand.</span>
                  </h1>
                  <p>
                    Discover, compare, and choose MCP servers with evidence
                    <br className="desktop-break" /> you can inspect. Build your next toolkit with
                    confidence.
                  </p>
                </div>
                <div className="hero-diagram" aria-hidden="true">
                  <div className="diagram-sources">
                    <span>GitHub</span>
                    <span>Notion</span>
                    <span>Slack</span>
                  </div>
                  <div className="diagram-line" />
                  <div className="diagram-center">
                    <Layers3 size={25} />
                    <span>Analyze & rank</span>
                  </div>
                  <div className="diagram-line" />
                  <div className="diagram-result">
                    <Check size={22} />
                    <span>Your shortlist</span>
                  </div>
                </div>
              </section>
              <form
                className="search-box"
                onSubmit={(event) => {
                  event.preventDefault();
                  void search();
                }}
              >
                <Search size={21} />
                <label className="sr-only" htmlFor="query">
                  Search servers
                </label>
                <input
                  id="query"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="What do you want to connect or build?"
                  maxLength={500}
                />
                <button className="primary" disabled={busy} type="submit">
                  {busy ? 'Searching…' : 'Find servers'}
                  <ArrowRight size={16} />
                </button>
              </form>
              <div className="suggestions">
                <span>TRY A USE CASE</span>
                {['Code reviews', 'Local database', 'Design components'].map((value) => (
                  <button
                    key={value}
                    onClick={() => {
                      setQuery(value);
                      void search(value);
                    }}
                  >
                    {value}
                    <ArrowRight size={12} />
                  </button>
                ))}
              </div>
              <div className="filter-bar">
                <SlidersHorizontal size={16} />
                <label>
                  <span className="sr-only">Category</span>
                  <select value={category} onChange={(event) => setCategory(event.target.value)}>
                    <option value="">All categories</option>
                    {categories.map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span className="sr-only">Deployment</span>
                  <select
                    value={deployment}
                    onChange={(event) => setDeployment(event.target.value)}
                  >
                    <option value="">Any deployment</option>
                    <option value="local">Local</option>
                    <option value="remote">Remote</option>
                  </select>
                </label>
                <button className="text-button" onClick={() => void search()}>
                  Apply filters
                </button>
                <label className="profile-select">
                  <span>Ranking</span>
                  <select
                    aria-label="Ranking profile"
                    value={profile}
                    onChange={(event) => setProfile(event.target.value as Profile)}
                  >
                    {profiles.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="results-header">
                <h2>
                  {ranking?.query ? 'Matching servers' : 'Explore the catalog'}{' '}
                  <span>{ranking?.results.length ?? '—'}</span>
                </h2>
                <span>Evidence-linked · versioned · inspectable</span>
              </div>
              {ranking?.fallback_reason && (
                <div className="alert" role="status">
                  The selected mock profile returned {ranking.fallback_reason}. Results use the
                  evidence baseline.
                </div>
              )}
              {!ranking && !error && (
                <p className="empty" role="status">
                  Loading the local catalog…
                </p>
              )}
              {ranking?.results.length === 0 && (
                <div className="empty">
                  <Search size={30} />
                  <h3>No matching servers</h3>
                  <p>Try a different capability or remove a filter.</p>
                </div>
              )}
              <div className="server-grid" aria-busy={busy}>
                {ranking?.results.map((item, index) => {
                  const server = item.server;
                  const checked = selected.some((s) => s.id === server.id);
                  return (
                    <article className={`server-card ${checked ? 'selected' : ''}`} key={server.id}>
                      <div className="card-top">
                        <span className={`server-icon color-${index % 5}`}>
                          {server.name.slice(0, 2)}
                        </span>
                        <span className="category">{server.category}</span>
                        <label className="select-control">
                          <input
                            type="checkbox"
                            aria-label={`Select ${server.name}`}
                            checked={checked}
                            disabled={!checked && selected.length >= 4}
                            onChange={() => toggle(server)}
                          />
                        </label>
                      </div>
                      <button className="server-title" onClick={() => setDetail(server)}>
                        {server.name}
                        <ChevronRight size={16} />
                      </button>
                      <p>
                        {server.description
                          .replace(/^Simulated /, '')
                          .replace(' integration for ', ': ')}
                      </p>
                      <div className="tags">
                        {server.tags.slice(0, 3).map((tag) => (
                          <span key={tag}>{tag}</span>
                        ))}
                      </div>
                      <div className="card-footer">
                        <span className="transport">
                          <span className="status-dot" />
                          {server.transport}
                        </span>
                        <span>
                          {server.evidence.length} evidence item
                          {server.evidence.length !== 1 ? 's' : ''}
                        </span>
                      </div>
                    </article>
                  );
                })}
              </div>
              <div className="catalog-note">
                <CircleHelp size={15} />
                <span>
                  30 synthetic server records. Capabilities and versions are illustrative, with no
                  vendor verification.
                </span>
              </div>
              {selected.length > 0 && (
                <div className="selection-tray">
                  <div>
                    <strong>{selected.length} selected</strong>
                    <span>{selected.map((s) => s.name).join(' · ')}</span>
                  </div>
                  <button className="text-button" onClick={() => setSelected([])}>
                    Clear
                  </button>
                  <button className="primary" onClick={() => setCompare(true)}>
                    <GitCompareArrows size={17} />
                    {selected.length > 1 ? 'Compare & save' : 'Save selection'}
                  </button>
                </div>
              )}
            </>
          )}

          {tab === 'Collections' && (
            <CollectionsView
              collections={collections}
              exportCollection={exportCollection}
              importCollection={importCollection}
              navigate={navigate}
            />
          )}
          {tab === 'Evaluations' && (
            <EvaluationsView
              profile={profile}
              setProfile={setProfile}
              busy={busy}
              runEvaluation={runEvaluation}
              report={report}
              reports={reports}
              setReport={setReport}
            />
          )}
          {tab === 'Versions' && (
            <>
              <PageTitle
                eyebrow="REPRODUCIBLE BY DESIGN"
                title="A catalog with a memory."
                description="Every snapshot keeps its own search index. Compare changes without rewriting the past."
              />
              <section className="panel">
                <span className="eyebrow">ACTIVE SNAPSHOT</span>
                <h2>Immutable catalog</h2>
                <code className="hash">{versions?.active_catalog ?? 'Loading…'}</code>
                <p>
                  Saved collections retain their original server versions when the active catalog
                  changes.
                </p>
              </section>
              <section className="panel">
                <h2>Compare snapshots</h2>
                <div className="inline-form">
                  <select
                    aria-label="Earlier snapshot"
                    value={previous}
                    onChange={(event) => setPrevious(event.target.value)}
                  >
                    <option value="">Choose a snapshot</option>
                    {versions?.snapshots.map((item) => (
                      <option key={item} value={item}>
                        {item.slice(0, 20)}
                      </option>
                    ))}
                  </select>
                  <button
                    className="secondary"
                    disabled={!previous || !versions}
                    onClick={async () => {
                      try {
                        setDiff(
                          await api(
                            `/versions/diff?before=${previous}&after=${versions!.active_catalog}`,
                          ),
                        );
                      } catch (cause) {
                        setError(message(cause));
                      }
                    }}
                  >
                    Compare with active
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
              </section>
            </>
          )}
        </main>
        <footer className="workspace-footer">
          <span>MCP SERVER SORTER</span>
          <span>Local data. Inspectable decisions.</span>
        </footer>
      </div>

      <dialog
        ref={dialog}
        className="detail-dialog"
        aria-label={detail ? `${detail.name} details` : 'Compare selected servers'}
        onCancel={() => {
          setDetail(null);
          setCompare(false);
        }}
        onClose={() => {
          setDetail(null);
          setCompare(false);
        }}
      >
        <button
          className="icon-button close-dialog"
          aria-label="Close details"
          onClick={() => {
            setDetail(null);
            setCompare(false);
          }}
        >
          <X size={21} />
        </button>
        {detail && (
          <>
            <span className="eyebrow">SERVER DETAILS · SIMULATED</span>
            <h2>{detail.name}</h2>
            <p>{detail.description}</p>
            <dl className="facts">
              <dt>Version</dt>
              <dd>{detail.version}</dd>
              <dt>Transport</dt>
              <dd>{detail.transport}</dd>
              <dt>Authentication</dt>
              <dd>{detail.auth}</dd>
              <dt>License</dt>
              <dd>{detail.license ?? 'Unknown'}</dd>
              <dt>Status</dt>
              <dd>{detail.status}</dd>
            </dl>
            <h3>Supporting evidence</h3>
            {detail.evidence.map((item) => (
              <div className="evidence" key={item.id}>
                <code>{item.id}</code>
                <p>{item.statement}</p>
                <span>Fixture reference · illustrative metadata</span>
              </div>
            ))}
          </>
        )}
        {compare && (
          <>
            <span className="eyebrow">YOUR SHORTLIST</span>
            <h2>Compare & keep</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Server</th>
                    <th>Transport</th>
                    <th>Auth</th>
                    <th>License</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.map((item) => (
                    <tr key={item.id}>
                      <td>{item.name}</td>
                      <td>{item.transport}</td>
                      <td>{item.auth}</td>
                      <td>{item.license ?? 'Unknown'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <form className="save-form" onSubmit={(event) => void save(event)}>
              <label htmlFor="collection-name">Collection name</label>
              <input
                id="collection-name"
                value={collectionName}
                onChange={(event) => setCollectionName(event.target.value)}
                maxLength={80}
                required
              />
              <button className="primary" disabled={busy || !collectionName.trim()} type="submit">
                Save collection
                <Check size={17} />
              </button>
            </form>
          </>
        )}
      </dialog>
    </div>
  );
}

function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : 'Something went wrong. Please try again.';
}
