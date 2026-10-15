import type { Ranking } from '../api';

interface Props {
  explanation: NonNullable<Ranking['results'][number]['retrieval']>;
  mode: string;
}

export default function RankingEvidence({ explanation, mode }: Props) {
  const filters = Object.entries(explanation.applied_filters)
    .filter(([key, value]) => key !== 'include_deprecated' && value != null)
    .map(([key, value]) => `${key}: ${value}`);
  return (
    <details className="ranking-evidence">
      <summary>Why this match?</summary>
      <p>
        {explanation.matched_fields.length
          ? `Search terms matched the ${explanation.matched_fields.join(', ')}.`
          : 'Included by catalog browsing; no search terms were applied.'}
      </p>
      <p>
        {filters.length
          ? `Satisfied filters: ${filters.join('; ')}.`
          : 'No metadata filters applied.'}
        {!explanation.applied_filters.include_deprecated && ' Deprecated servers are excluded.'}
      </p>
      <p>
        BM25 score: {explanation.bm25_score.toPrecision(4)}. Lower scores sort first; this is not a
        confidence estimate. Equal scores use evidence count, freshness, then server ID.
      </p>
      <dl className="metadata-grid">
        <dt>Evidence items</dt>
        <dd>{explanation.evidence_count}</dd>
        <dt>Freshness used</dt>
        <dd>{new Date(explanation.freshness_at).toISOString().slice(0, 10)}</dd>
        <dt>Stable ID</dt>
        <dd>{explanation.stable_id}</dd>
      </dl>
      {explanation.evidence_ids.map((id) => (
        <code key={id}>{id} </code>
      ))}
      {(mode === 'simulated-rerank' || mode === 'live-rerank') && (
        <p>
          These facts explain retrieval. The top candidates were subsequently reordered by the
          selected {mode === 'simulated-rerank' ? 'mock' : 'live'} profile.
        </p>
      )}
    </details>
  );
}
