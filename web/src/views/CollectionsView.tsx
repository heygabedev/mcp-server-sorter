import { ArrowDownToLine, ArrowRight, Folder } from 'lucide-react';
import { useState } from 'react';
import type { CollectionRevision, Selection } from '../api';
import { api } from '../api';
import PageTitle from './PageTitle';
interface Props {
  collections: Selection[];
  exportCollection: (selection: Selection, revision?: number) => Promise<void>;
  importCollection: (file?: File) => Promise<void>;
  navigate: (tab: 'Discover') => Promise<void>;
  reloadCollections: () => Promise<void>;
}
export default function CollectionsView({
  collections,
  exportCollection,
  importCollection,
  navigate,
  reloadCollections,
}: Props) {
  const [detail, setDetail] = useState<CollectionRevision | null>(null);
  const [history, setHistory] = useState<CollectionRevision[]>([]);
  const [deleted, setDeleted] = useState<CollectionRevision[]>([]);
  const [showDeleted, setShowDeleted] = useState(false);
  const [name, setName] = useState('');
  const [ids, setIds] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  function select(revision: CollectionRevision) {
    setDetail(revision);
    setName(revision.selection.name);
    setIds(revision.selection.servers.map((server) => server.id).join('\n'));
  }

  async function manage(id: string) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const [current, revisions] = await Promise.all([
        api<CollectionRevision>(`/collections/${id}`),
        api<CollectionRevision[]>(`/collections/${id}/history`),
      ]);
      select(current);
      setHistory(revisions);
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  }

  async function change(current: CollectionRevision, action: 'edit' | 'delete' | 'restore') {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const updated = await api<CollectionRevision>(
        `/collections/${current.selection.id}/${action}`,
        {
          expected_revision: current.revision,
          ...(action === 'edit' ? { name, ids: ids.split(/\s+/).filter(Boolean) } : {}),
        },
      );
      select(updated);
      const [revisions, removed] = await Promise.all([
        api<CollectionRevision[]>(`/collections/${current.selection.id}/history`),
        api<CollectionRevision[]>('/collections/deleted'),
        reloadCollections(),
      ]);
      setHistory(revisions);
      setDeleted(removed);
      setNotice(
        action === 'delete'
          ? 'Collection deleted. Its history remains available for restoration.'
          : `Saved revision ${updated.revision}.`,
      );
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  }

  async function toggleDeleted(checked: boolean) {
    setShowDeleted(checked);
    if (!checked) return;
    try {
      setDeleted(await api('/collections/deleted'));
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="YOUR TOOLKIT"
        title="Keep the good connections."
        description="Pinned server versions and their evidence, ready to revisit or share."
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
      <label className="secondary file-label">
        Import selection
        <input
          type="file"
          accept="application/json,.json"
          onChange={(event) => void importCollection(event.target.files?.[0])}
        />
      </label>
      <label className="collection-toggle">
        <input
          type="checkbox"
          checked={showDeleted}
          onChange={(event) => void toggleDeleted(event.target.checked)}
        />
        Show deleted collections
      </label>
      {collections.length === 0 && (
        <div className="empty">
          <Folder size={32} />
          <h2>Your toolkit starts here.</h2>
          <p>Select servers in Discover, then save a collection.</p>
          <button className="primary" onClick={() => void navigate('Discover')}>
            Explore servers
            <ArrowRight size={16} />
          </button>
        </div>
      )}
      <div className="collection-grid">
        {collections.map((item) => (
          <article className="panel" key={item.id}>
            <Folder size={24} />
            <h2>{item.name}</h2>
            <p>{item.servers.map((s) => s.name).join(' · ')}</p>
            <small>
              {item.servers.length} pinned versions ·{' '}
              {new Date(item.created_at).toLocaleDateString()}
            </small>
            <code>{item.snapshot.slice(0, 16)}</code>
            <button className="secondary" onClick={() => void exportCollection(item)}>
              <ArrowDownToLine size={16} />
              Export selection
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void manage(item.id)}
              aria-label={`Manage ${item.name}`}
            >
              Manage collection
            </button>
          </article>
        ))}
      </div>
      {showDeleted && (
        <section aria-label="Deleted collections">
          <h2>Deleted collections</h2>
          {deleted.length === 0 && <p>No deleted collections.</p>}
          {deleted.map((item) => (
            <article className="panel" key={item.selection.id}>
              <h3>{item.selection.name}</h3>
              <p>Deleted at revision {item.revision}. Earlier revisions remain available.</p>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void manage(item.selection.id)}
              >
                Inspect deleted collection
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void change(item, 'restore')}
              >
                Restore collection
              </button>
            </article>
          ))}
        </section>
      )}
      {detail && (
        <section className="panel collection-editor" aria-label="Collection editor">
          <h2>
            {detail.selection.name} · revision {detail.revision}
          </h2>
          <button
            className="text-button"
            disabled={busy}
            onClick={() => void manage(detail.selection.id)}
          >
            Reload collection
          </button>
          {detail.deleted ? (
            <p>This collection is deleted. Restore it from deleted collections to edit it.</p>
          ) : (
            <>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void change(detail, 'edit');
                }}
              >
                <label htmlFor="edit-collection-name">Collection name</label>
                <input
                  id="edit-collection-name"
                  value={name}
                  required
                  maxLength={80}
                  onChange={(event) => setName(event.target.value)}
                />
                <label htmlFor="edit-collection-ids">Server IDs, one per line</label>
                <textarea
                  id="edit-collection-ids"
                  value={ids}
                  rows={5}
                  required
                  onChange={(event) => setIds(event.target.value)}
                />
                <p>
                  Membership uses this collection’s pinned catalog. Existing records stay pinned.
                </p>
                <button className="primary" disabled={busy} type="submit">
                  Save changes
                </button>
              </form>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void change(detail, 'delete')}
              >
                Delete collection
              </button>
            </>
          )}
          <h3>Revision history</h3>
          {history.map((item) => (
            <div className="evidence" key={item.revision}>
              <strong>
                Revision {item.revision} · {item.selection.name}
                {item.deleted ? ' · deleted' : ''}
              </strong>
              <p>{item.selection.servers.map((server) => server.name).join(' · ')}</p>
              <button
                className="text-button"
                onClick={() => void exportCollection(item.selection, item.revision)}
              >
                Export revision {item.revision}
              </button>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
