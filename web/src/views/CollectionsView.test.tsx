import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import CollectionsView from './CollectionsView';
import type { Selection } from '../api';
import catalog from '../../../src/mcp_sorter/data/catalog.json';

const selection = {
  schema_version: 1,
  id: 'a'.repeat(32),
  name: 'My tools',
  snapshot: 'b'.repeat(64),
  created_at: '2026-10-15T12:00:00Z',
  servers: catalog.records.slice(0, 2),
} as Selection;
const revision = { revision: 1, deleted: false, changed_at: selection.created_at, selection };

test('exports a historical revision and reports stale edits without discarding the form', async () => {
  const exported = vi.fn();
  const refreshed = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path.endsWith('/edit')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          expected_revision: 1,
          name: 'Renamed',
          ids: selection.servers.map((s) => s.id),
        });
        return Response.json(
          { detail: 'Collection changed; reload before editing' },
          { status: 409 },
        );
      }
      return Response.json(path.endsWith('/history') ? [revision] : revision);
    }),
  );
  render(
    <CollectionsView
      collections={[selection]}
      exportCollection={exported}
      importCollection={vi.fn()}
      navigate={vi.fn()}
      reloadCollections={refreshed}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Manage My tools' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Export revision 1' }));
  expect(exported).toHaveBeenCalledWith(selection, 1);
  fireEvent.change(screen.getByLabelText('Collection name'), { target: { value: 'Renamed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Collection changed; reload before editing',
  );
  expect(screen.getByLabelText('Collection name')).toHaveValue('Renamed');
  expect(refreshed).not.toHaveBeenCalled();
});

test('requires an explicit choice to remove missing servers from a preview', async () => {
  const preview = {
    collection_id: selection.id,
    revision: 1,
    snapshot: 'c'.repeat(64),
    preview_token: 'token',
    changes: [
      {
        before: selection.servers[0],
        after: { ...selection.servers[0], version: '2-demo', status: 'deprecated' },
        changed_fields: ['version', 'status'],
      },
      { before: selection.servers[1], after: null, changed_fields: [] },
    ],
  };
  const updated = { ...revision, revision: 2 };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path.endsWith('/drift')) return Response.json(preview);
      if (path.endsWith('/update-catalog')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          expected_revision: 1,
          preview_token: 'token',
          remove_missing: true,
        });
        return Response.json(updated);
      }
      if (path.endsWith('/history')) return Response.json([revision]);
      if (path.endsWith('/deleted')) return Response.json([]);
      return Response.json(revision);
    }),
  );
  render(
    <CollectionsView
      collections={[selection]}
      exportCollection={vi.fn()}
      importCollection={vi.fn()}
      navigate={vi.fn()}
      reloadCollections={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Manage My tools' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Preview catalog update' }));
  expect(await screen.findByText('Missing from the active catalog')).toBeInTheDocument();
  expect(screen.getByText('version, status · deprecated')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Update catalog pins' })).toBeDisabled();
  fireEvent.click(screen.getByLabelText('Remove missing servers when updating'));
  fireEvent.click(screen.getByRole('button', { name: 'Update catalog pins' }));
  expect(await screen.findByText('Saved revision 2.')).toBeInTheDocument();
});
