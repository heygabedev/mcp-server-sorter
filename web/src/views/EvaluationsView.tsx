import { ArrowDownToLine, ArrowRight, ChevronRight } from 'lucide-react';
import { download } from '../api';
import type { Profile, Report } from '../api';
import { profiles } from '../profiles';
import PageTitle from './PageTitle';
interface Props {
  profile: Profile;
  setProfile: (profile: Profile) => void;
  busy: boolean;
  runEvaluation: () => Promise<void>;
  report: Report | null;
  reports: Report[];
  setReport: (report: Report) => void;
}
export default function EvaluationsView({
  profile,
  setProfile,
  busy,
  runEvaluation,
  report,
  reports,
  setReport,
}: Props) {
  return (
    <>
      <PageTitle
        eyebrow="MEASURE, THEN COMPARE"
        title="Make every ranking accountable."
        description="Run the versioned golden fixture set and inspect each result, constraint, and evidence reference."
      />
      <div className="evaluation-intro">
        <div className="stat">
          <strong>90</strong>
          <span>golden cases</span>
        </div>
        <div className="stat">
          <strong>30 / 60</strong>
          <span>development / held-out</span>
        </div>
        <div className="stat">
          <strong>$0</strong>
          <span>external model spend</span>
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
            {busy ? 'Running 90 cases…' : 'Run evaluation'}
            <ArrowRight size={16} />
          </button>
        </div>
      </div>
      <p className="caption">
        Synthetic regression benchmark. These results do not measure live model quality.
      </p>
      {report && (
        <section className="panel report">
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
            ].map(([label, key]) => (
              <div className="stat" key={key}>
                <strong>{report.summary[key]?.toFixed(2) ?? '—'}</strong>
                <span>{label}</span>
              </div>
            ))}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Split</th>
                  <th>Top result</th>
                  <th>NDCG@5</th>
                  <th>Constraints</th>
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
