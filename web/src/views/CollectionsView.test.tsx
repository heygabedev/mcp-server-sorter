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
