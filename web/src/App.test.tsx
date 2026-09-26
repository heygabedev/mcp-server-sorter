import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import App from './App';
import catalog from '../../src/mcp_sorter/data/catalog.json';
import type { WorkspaceInfo } from './api';

const records = catalog.records.slice(0, 3);
const ranking = {
  snapshot: 'a'.repeat(64),
  query: '',
  results: records.map((server) => ({ server, relevance: 1, reasons: [], evidence_ids: [] })),
};
const workspace: WorkspaceInfo = {
  application_version: '0.3.1rc2',
  mode: 'demo',
  catalog: { snapshot: ranking.snapshot, total: 73, simulated: 73 },
  evaluation_dataset: { version: 'fixture-test', total: 12, development: 4, heldout: 8 },
};

function mockEndpoints(overrides: Record<string, unknown> = {}) {
  const responses: Record<string, unknown> = {
    '/api/v1/workspace': workspace,
    '/api/v1/servers': ranking,
    '/api/v1/rankings': ranking,
    '/api/v1/collections': [],
    '/api/v1/evaluations': [],
    ...overrides,
  };
  vi.mocked(fetch).mockImplementation(async (path) => {
    const body = responses[String(path)];
    if (body === undefined) throw new Error(`Unexpected API request: ${path}`);
    return body instanceof Response ? body : Response.json(body);
  });
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn());
  mockEndpoints();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

test('shows the failed evaluation scope and reason', async () => {
  const saved = {
    id: 'report',
    profile: 'baseline',
    cases: [],
    summary: {},
    dataset_version: 'v1',
  };
  mockEndpoints({
    '/api/v1/evaluations': [saved],
    '/api/v1/evaluation-comparison?baseline=report&candidate=report': {
      ndcg_delta: 0,
      ci95: [0, 0],
      passes_regression_gate: false,
      gate_policy_version: 'fixture-regression-v2',
      failures: [{ scope: 'heldout', code: 'incorrect-abstention' }],
    },
  });
  render(<App />);
  await screen.findByRole('button', { name: 'GitHub' });
  fireEvent.click(screen.getByRole('button', { name: 'Evaluations' }));
  fireEvent.click(await screen.findByRole('button', { name: /baseline.*v1/ }));
  fireEvent.change(screen.getByLabelText('Baseline evaluation'), { target: { value: 'report' } });
  fireEvent.click(screen.getByRole('button', { name: 'Compare baseline' }));
  expect(await screen.findByText(/heldout: incorrect abstention/)).toHaveTextContent(
    'Paired regression gate: failed',
  );
});

test('loads the catalog and exposes inspectable details', async () => {
  render(<App />);
  expect(await screen.findByRole('button', { name: 'GitHub' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'GitHub' }));
  expect(await screen.findByText('Supporting evidence')).toBeInTheDocument();
  expect(screen.getByText('fixture.github.capabilities')).toBeInTheDocument();
});

test.each([
  { button: 'Find servers', category: 'database', deployment: 'local', profile: 'demo-balanced' },
  { button: 'Apply filters', category: 'development', deployment: 'remote', profile: 'demo-fast' },
])(
  'sends every search option through $button',
  async ({ button, category, deployment, profile }) => {
    render(<App />);
    await screen.findByRole('button', { name: 'GitHub' });
    fireEvent.change(screen.getByLabelText('Search servers'), {
      target: { value: 'pull requests' },
    });
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: category } });
    fireEvent.change(screen.getByLabelText('Deployment'), { target: { value: deployment } });
    fireEvent.change(screen.getByLabelText('Ranking profile'), { target: { value: profile } });
    fireEvent.click(screen.getByRole('button', { name: button }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/rankings',
        expect.objectContaining({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    const request = vi.mocked(fetch).mock.calls.find(([path]) => path === '/api/v1/rankings');
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      query: 'pull requests',
      filters: { category, deployment },
      profile,
      limit: 50,
    });
  },
);

test('clearing category and deployment removes both constraints from the next request', async () => {
  render(<App />);
  await screen.findByRole('button', { name: 'GitHub' });
  fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'database' } });
  fireEvent.change(screen.getByLabelText('Deployment'), { target: { value: 'local' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Find servers' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Category'), { target: { value: '' } });
  fireEvent.change(screen.getByLabelText('Deployment'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
  await waitFor(() => {
    const requests = vi.mocked(fetch).mock.calls.filter(([path]) => path === '/api/v1/rankings');
    expect(requests).toHaveLength(2);
    expect(JSON.parse(String(requests[1][1]?.body))).toEqual({
      query: '',
      filters: { category: null, deployment: null },
      profile: 'baseline',
      limit: 50,
    });
  });
});

test('a delayed initial catalog cannot replace newer filtered results', async () => {
  let resolveCatalog!: (response: Response) => void;
  const initial = new Promise<Response>((resolve) => {
    resolveCatalog = resolve;
  });
  const filtered = { ...ranking, query: 'github', results: ranking.results.slice(0, 1) };
  vi.mocked(fetch).mockImplementation(async (path) => {
    if (path === '/api/v1/servers') return initial;
    return Response.json(path === '/api/v1/workspace' ? workspace : filtered);
  });
  render(<App />);
  fireEvent.change(screen.getByLabelText('Search servers'), { target: { value: 'github' } });
  fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'development' } });
  fireEvent.click(screen.getByRole('button', { name: 'Find servers' }));
  await screen.findByRole('heading', { name: 'Matching servers 1' });
  await act(async () => resolveCatalog(Response.json(ranking)));
  expect(screen.getByRole('heading', { name: 'Matching servers 1' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Slack' })).not.toBeInTheDocument();
});

test('shows a recoverable service error', async () => {
  mockEndpoints({
    '/api/v1/servers': Response.json({ detail: 'Catalog unavailable' }, { status: 503 }),
  });
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
  mockEndpoints({
    '/api/v1/servers': {
      ...ranking,
      results: [{ ...ranking.results[0], server: { ...records[0], description } }],
    },
  });
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: 'GitHub' }));
  expect(screen.getAllByText(description).length).toBeGreaterThan(0);
  expect(document.querySelector('img[onerror]')).toBeNull();
});

test('uses runtime metadata for version and total count independently of search results', async () => {
  render(<App />);
  await screen.findByRole('button', { name: 'GitHub' });
  expect(screen.getByLabelText('Application status')).toHaveTextContent('Offline showcase');
  expect(screen.getByLabelText('Application status')).toHaveTextContent('v0.3.1rc2');
  expect(screen.getByLabelText('Total catalog records')).toHaveTextContent('73');
  expect(screen.getByText(/73 synthetic server records/)).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Explore the catalog 3' })).toBeInTheDocument();
});

test('distinguishes live mode from a catalog containing synthetic records', async () => {
  mockEndpoints({
    '/api/v1/workspace': {
      ...workspace,
      mode: 'live',
      catalog: { ...workspace.catalog, simulated: 2 },
    },
    '/api/v1/servers': {
      ...ranking,
      results: [
        {
          ...ranking.results[0],
          server: {
            ...records[0],
            simulated: false,
            evidence: [{ ...records[0].evidence[0], kind: 'registry' }],
          },
        },
      ],
    },
  });
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: 'GitHub' }));
  expect(screen.getByLabelText('Application status')).toHaveTextContent('Live mode');
  expect(screen.queryByText('Offline showcase')).not.toBeInTheDocument();
  expect(screen.getByText('MIXED DATA')).toBeInTheDocument();
  expect(screen.getByText(/73 server records, including 2 synthetic/)).toBeInTheDocument();
  expect(screen.getByText('SERVER DETAILS · SOURCE METADATA')).toBeInTheDocument();
  expect(
    screen.getByText('registry reference · unverified source declaration'),
  ).toBeInTheDocument();
});

test('shows unavailable metadata without inventing an offline mode or version, then recovers on focus', async () => {
  mockEndpoints({ '/api/v1/workspace': Response.json({}, { status: 503 }) });
  render(<App />);
  await screen.findByText('Status unavailable');
  expect(screen.getByLabelText('Total catalog records')).toHaveTextContent('—');
  expect(screen.queryByText('Offline showcase')).not.toBeInTheDocument();
  expect(screen.queryByText('v0.1.0')).not.toBeInTheDocument();
  mockEndpoints({ '/api/v1/workspace': { ...workspace, catalog: null, mode: 'live' } });
  fireEvent(window, new Event('focus'));
  await screen.findByText('Live mode');
  expect(screen.getByLabelText('Total catalog records')).toHaveTextContent('0');
  expect(screen.getByText('No active catalog.')).toBeInTheDocument();
});

test('refreshes catalog totals immediately after version activation', async () => {
  const older = 'b'.repeat(64);
  const configuration = 'c'.repeat(64);
  const versions = {
    active_catalog: ranking.snapshot,
    active_configuration: configuration,
    snapshots: [ranking.snapshot, older],
    configurations: { [configuration]: { name: 'Default', version: 'v1', name_weight: 5 } },
  };
  const endpoints = {
    '/api/v1/versions': versions,
    '/api/v1/backups': [],
    '/api/v1/versions/activate': {},
  };
  mockEndpoints(endpoints);
  render(<App />);
  await screen.findByRole('button', { name: 'GitHub' });
  fireEvent.click(screen.getByRole('button', { name: 'Versions' }));
  await screen.findByLabelText('Catalog snapshot');
  mockEndpoints({
    ...endpoints,
    '/api/v1/workspace': { ...workspace, catalog: { snapshot: older, total: 8, simulated: 0 } },
  });
  fireEvent.change(screen.getByLabelText('Catalog snapshot'), { target: { value: older } });
  fireEvent.click(screen.getByRole('button', { name: 'Activate selected versions' }));
  await screen.findByText(/Catalog and configuration activated together/);
  expect(screen.getByLabelText('Total catalog records')).toHaveTextContent('8');
  expect(screen.getByText('SOURCE METADATA')).toBeInTheDocument();
});

test('refreshes catalog totals when a refresh job completes', async () => {
  const job = {
    id: 'job-1',
    status: 'completed',
    payload: '{"kind":"catalog-refresh"}',
    attempts: 1,
  };
  const endpoints = {
    '/api/v1/operations': {
      mode: 'demo',
      worker_enabled: true,
      telemetry: 'Local only',
      jobs: [],
      events: [],
      traces: [],
      alerts: [],
    },
    '/api/v1/jobs': { ...job, status: 'queued' },
    '/api/v1/jobs/job-1': job,
  };
  mockEndpoints(endpoints);
  render(<App />);
  await screen.findByRole('button', { name: 'GitHub' });
  fireEvent.click(screen.getByRole('button', { name: 'Operations' }));
  await screen.findByRole('button', { name: 'Refresh catalog' });
  mockEndpoints({
    ...endpoints,
    '/api/v1/workspace': {
      ...workspace,
      catalog: { snapshot: 'b'.repeat(64), total: 81, simulated: 81 },
    },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh catalog' }));
  await waitFor(() =>
    expect(screen.getByLabelText('Total catalog records')).toHaveTextContent('81'),
  );
});

test.each([
  { cost: null, expectedCost: 'Not reported' },
  { cost: 0, expectedCost: '$0.00' },
  { cost: 0.125, expectedCost: '$0.13' },
])('uses dataset metadata and the saved report cost $cost', async ({ cost, expectedCost }) => {
  const saved = {
    id: 'report-1',
    profile: 'baseline',
    dataset_version: 'old-fixture',
    summary: { cost_usd: cost },
    cases: [
      { id: 'case-1', split: 'heldout', result_ids: [], metrics: {}, constraint_violations: 0 },
    ],
  };
  mockEndpoints({ '/api/v1/evaluations': [saved] });
  render(<App />);
  await screen.findByRole('button', { name: 'GitHub' });
  fireEvent.click(screen.getByRole('button', { name: 'Evaluations' }));
  const row = await screen.findByRole('button', { name: /old-fixture/ });
  expect(screen.getByText('12')).toBeInTheDocument();
  expect(screen.getByText('4 / 8')).toBeInTheDocument();
  expect(screen.getByText('Not reported')).toBeInTheDocument();
  fireEvent.click(row);
  expect(screen.getByText('0 / 1')).toBeInTheDocument();
  expect(screen.queryByText('4 / 8')).not.toBeInTheDocument();
  expect(screen.getByText(expectedCost)).toBeInTheDocument();
});
