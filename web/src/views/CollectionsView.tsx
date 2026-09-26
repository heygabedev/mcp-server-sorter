import { ArrowDownToLine, ArrowRight, Folder } from 'lucide-react';
import type { Selection } from '../api';
import PageTitle from './PageTitle';
interface Props {
  collections: Selection[];
  exportCollection: (selection: Selection) => Promise<void>;
  importCollection: (file?: File) => Promise<void>;
  navigate: (tab: 'Discover') => Promise<void>;
}
export default function CollectionsView({
  collections,
  exportCollection,
  importCollection,
  navigate,
}: Props) {
  return (
    <>
      <PageTitle
        eyebrow="YOUR TOOLKIT"
        title="Keep the good connections."
        description="Pinned server versions and their evidence, ready to revisit or share."
      />
      <label className="secondary file-label">
        Import selection
        <input
          type="file"
          accept="application/json,.json"
          onChange={(event) => void importCollection(event.target.files?.[0])}
        />
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
          </article>
        ))}
      </div>
    </>
  );
}
