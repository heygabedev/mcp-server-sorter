import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import RankingEvidence from './RankingEvidence';
import type { Ranking } from '../api';

const explanation: NonNullable<Ranking['results'][number]['retrieval']> = {
  version: 'retrieval-evidence-v1',
  matched_fields: ['description', 'tags'],
  applied_filters: { category: '<script>poisoned</script>', include_deprecated: false },
  bm25_score: -0.75,
  evidence_count: 2,
  evidence_ids: ['fixture.test.capability'],
  freshness_at: '2026-01-01T00:00:00Z',
  stable_id: 'demo/test',
};

test('explains field matches and filters as escaped text', () => {
  const { container } = render(
    <RankingEvidence explanation={explanation} mode="simulated-rerank" />,
  );
  fireEvent.click(screen.getByText('Why this match?'));
  expect(screen.getByText('Search terms matched the description, tags.')).toBeVisible();
  expect(screen.getByText(/category: <script>poisoned<\/script>/)).toBeVisible();
  expect(container.querySelector('script')).toBeNull();
  expect(screen.getByText(/not a confidence estimate/)).toBeVisible();
  expect(screen.getByText(/subsequently reordered.*mock/)).toBeVisible();
  expect(screen.getByText('fixture.test.capability')).toBeVisible();
});

test('labels browsing without claiming a term match or model ordering', () => {
  render(
    <RankingEvidence
      explanation={{
        ...explanation,
        matched_fields: [],
        applied_filters: { include_deprecated: false },
      }}
      mode="deterministic"
    />,
  );
  fireEvent.click(screen.getByText('Why this match?'));
  expect(screen.getByText(/no search terms were applied/)).toBeVisible();
  expect(screen.queryByText(/subsequently reordered/)).not.toBeInTheDocument();
});
