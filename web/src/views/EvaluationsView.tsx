import { ArrowDownToLine, ArrowRight, ChevronRight } from 'lucide-react';
import { api, download } from '../api';
import { useState } from 'react';
import type { Profile, Report, WorkspaceInfo } from '../api';
import { profiles } from '../profiles';
import PageTitle from './PageTitle';
interface Props {
  dataset: WorkspaceInfo['evaluation_dataset'] | null;
  profile: Profile;
  setProfile: (profile: Profile) => void;
  busy: boolean;
  runEvaluation: () => Promise<void>;
  report: Report | null;
  reports: Report[];
  setReport: (report: Report) => void;
}
export default function EvaluationsView({
  dataset,
  profile,
  setProfile,
  busy,
  runEvaluation,
  report,
  reports,
  setReport,
}: Props) {
  const total = report?.cases.length ?? dataset?.total;
  const development = report
    ? report.cases.filter((item) => item.split === 'development').length
    : dataset?.development;
  const heldout = report
    ? report.cases.filter((item) => item.split === 'heldout').length
    : dataset?.heldout;
  const cost = report?.summary.cost_usd;
  const [baseline, setBaseline] = useState('');
  const [comparison, setComparison] = useState('');
  async function compare() {
    if (!report || !baseline) return;
    try {
      const result = await api<{
        ndcg_delta: number | null;
        ci95: number[] | null;
        passes_regression_gate: boolean;
      }>(`/evaluation-comparison?baseline=${baseline}&candidate=${report.id}`);
      setComparison(
        `NDCG@5 change: ${result.ndcg_delta?.toFixed(3) ?? 'n/a'}. Paired regression gate: ${result.passes_regression_gate ? 'passed' : 'failed'}. 95% interval: ${result.ci95?.map((v) => v.toFixed(3)).join(' to ') ?? 'n/a'}.`,
      );
    } catch (cause) {
      setComparison(cause instanceof Error ? cause.message : 'Comparison failed');
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="MEASURE, THEN COMPARE"
        title="Make every ranking accountable."
        description="Run the versioned golden fixture set and inspect each result, constraint, and evidence reference."
      />
      <div className="evaluation-intro">
        <div className="stat">
          <strong>{total ?? '—'}</strong>
          <span>golden cases</span>
        </div>
        <div className="stat">
          <strong>
            {development ?? '—'} / {heldout ?? '—'}
          </strong>
          <span>development / held-out</span>
        </div>
        <div className="stat">
          <strong>{cost == null ? 'Not reported' : `$${cost.toFixed(2)}`}</strong>
          <span>reported model cost</span>
        </div>
        <div className="eval-actions">
          <select
            aria-label="Evaluation profile"
            value={profile}
            onChange={(event) => setProfile(event.target.value as Profile)}
          >
            {profiles.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <button className="primary" disabled={busy} onClick={() => void runEvaluation()}>
            {busy ? `Running ${dataset?.total ?? ''} cases…` : 'Run evaluation'}
            <ArrowRight size={16} />
          </button>
        </div>
      </div>
      <p className="caption">
        Synthetic regression benchmark. These results do not measure live model quality.
      </p>
      {report && (
        <section className="panel report">
          <div className="eval-actions">
            <select
              aria-label="Baseline evaluation"
              value={baseline}
              onChange={(event) => setBaseline(event.target.value)}
            >
              <option value="">Choose a baseline report</option>
              {reports.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.profile} · {item.id.slice(0, 8)}
                </option>
              ))}
            </select>
            <button className="secondary" disabled={!baseline} onClick={() => void compare()}>
              Compare baseline
            </button>
          </div>
          {comparison && (
            <p role="status" className="caption">
              {comparison}
            </p>
          )}
          <div className="section-heading">
            <div>
              <span className="eyebrow">LATEST RESULT · {report.profile}</span>
              <h2>Golden dataset report</h2>
            </div>
            <button
              className="secondary"
              onClick={() => download(`evaluation-${report.id}.json`, report)}
            >
              <ArrowDownToLine size={16} />
              Export report
            </button>
          </div>
          <div className="metric-grid">
            {[
              ['NDCG@5', 'ndcg_at_5'],
              ['Recall@10', 'recall_at_10'],
              ['Constraint violations', 'constraint_violations'],
              ['Fallback rate', 'fallback_rate'],
              ['Unsupported claim rate', 'unsupported_claim_rate'],
              ['Abstention rate', 'abstention_rate'],
            ].map(([label, key]) => (
              <div className="stat" key={key}>
                <strong>{report.summary[key]?.toFixed(2) ?? '—'}</strong>
                <span>{label}</span>
              </div>
            ))}
          </div>
          <p className="caption">{report.claim_check_scope}</p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Evaluation cases">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Split</th>
                  <th>Top result</th>
                  <th>NDCG@5</th>
                  <th>Constraints</th>
                  <th>Evidence errors</th>
                  <th>Unsupported claims</th>
                  <th>Fallback</th>
                </tr>
              </thead>
              <tbody>
                {report.cases.map((item) => (
                  <tr key={item.id}>
                    <td>{item.id}</td>
                    <td>{item.split}</td>
                    <td>{item.result_ids[0] ?? 'Abstained'}</td>
                    <td>{item.metrics.ndcg_at_5?.toFixed(3) ?? 'N/A'}</td>
                    <td>{item.constraint_violations === 0 ? 'Passed' : 'Failed'}</td>
                    <td>{item.invalid_evidence_references}</td>
                    <td>{item.unsupported_claims}</td>
                    <td>
                      {item.fallback ? String(item.model_metadata?.failure ?? 'Used') : 'None'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <h2 className="subheading">Run history</h2>
      {reports.length === 0 ? (
        <p className="empty">Run your first evaluation to establish a baseline.</p>
      ) : (
        reports.map((item) => (
          <button className="history-row" key={item.id} onClick={() => setReport(item)}>
            <span>
              <strong>{item.profile}</strong>
              <small>
                {item.dataset_version} · {item.id.slice(0, 8)}
              </small>
            </span>
            <span>
              NDCG@5 {item.summary.ndcg_at_5?.toFixed(3) ?? 'N/A'}
              <ChevronRight size={17} />
            </span>
          </button>
        ))
      )}
    </>
  );
}
