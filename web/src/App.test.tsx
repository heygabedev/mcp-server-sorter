import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import App from './App';
import catalog from '../../src/mcp_sorter/data/catalog.json';

const records = catalog.records.slice(0, 3);
const ranking = {
  snapshot: 'a'.repeat(64),
  results: records.map((server) => ({ server, relevance: 1, reasons: [], evidence_ids: [] })),
};

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ranking }));
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

test('loads the catalog and exposes inspectable details', async () => {
  render(<App />);
  expect(await screen.findByRole('button', { name: 'GitHub' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'GitHub' }));
  expect(await screen.findByText('Supporting evidence')).toBeInTheDocument();
  expect(screen.getByText('fixture.github.capabilities')).toBeInTheDocument();
});

test('sends query and filters to the shared ranking API', async () => {
  render(<App />);
  await screen.findByRole('button', { name: 'GitHub' });
  fireEvent.change(screen.getByLabelText('Search servers'), { target: { value: 'pull requests' } });
  fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'development' } });
  fireEvent.click(screen.getByRole('button', { name: 'Find servers' }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/rankings',
      expect.objectContaining({
        body: expect.stringContaining('pull requests'),
        method: 'POST',
      }),
    ),
  );
});

test('shows a recoverable service error', async () => {
  vi.mocked(fetch).mockResolvedValue({
    ok: false,
    status: 503,
    json: async () => ({ detail: 'Catalog unavailable' }),
  } as Response);
  render(<App />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Catalog unavailable');
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss error' }));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('keeps selected records ready to compare', async () => {
  render(<App />);
  await screen.findByRole('button', { name: 'GitHub' });
  fireEvent.click(screen.getByLabelText('Select GitHub'));
  fireEvent.click(screen.getByLabelText('Select Slack'));
  expect(screen.getByText('2 selected')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Compare & save' }));
  expect(screen.getByLabelText('Collection name')).toHaveValue('My toolkit');
});

test('renders poisoned metadata as text without creating executable elements', async () => {
  const description = '<img src=x onerror="window.compromised=true">';
  vi.mocked(fetch).mockResolvedValue({
    ok: true,
    json: async () => ({
      ...ranking,
      results: [{ ...ranking.results[0], server: { ...records[0], description } }],
    }),
  } as Response);
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: 'GitHub' }));
  expect(screen.getAllByText(description).length).toBeGreaterThan(0);
  expect(document.querySelector('img[onerror]')).toBeNull();
});
