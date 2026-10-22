/**
 * Panels.jsx - the three small read-only panels in the bottom strip.
 *
 *   PatternBars     how many flagged transactions each detector contributed to
 *   EvaluationPanel precision / recall / F1 ... measured against synthetic ground truth
 *   ScenarioPanel   recall per injected scenario + confusion matrix
 *   CrossValidationPanel  K-fold cross-validation: per-fold table, mean +- std, pooled out-of-fold result
 *
 * For UPLOADED data there are no ground-truth labels, so evaluation is null and the panels
 * explain that instead of showing made-up numbers.
 */
import { PAT, SCEN, pct } from '../lib/format.js';

/** Bar chart built from CSS grid: [label][bar][value] per row. */
export function PatternBars({ patterns }) {
  const entries = Object.entries(patterns || {}).sort((a, b) => b[1] - a[1]);
  const top = Math.max(...entries.map(([, v]) => v), 1);
  return (
    <>
      <h2>Patterns among flagged<small>transactions per pattern</small></h2>
      {!entries.length && <p className="note">Nothing was flagged.</p>}
      <div className="bars">
        {entries.map(([k, v]) => (
          <Row key={k} label={PAT[k] || k} width={(v / top) * 100} value={v} />
        ))}
      </div>
    </>
  );
}

/** One bar row (React fragment so the three cells become direct grid children). */
function Row({ label, width, value, color }) {
  return (
    <>
      <span>{label}</span>
      <div className="bar" style={{ width: `${width}%`, ...(color ? { background: color } : {}) }} />
      <span className="n">{value}</span>
    </>
  );
}

export function EvaluationPanel({ evaluation: E, meta }) {
  if (!E) {
    return (
      <>
        <h2>Detection quality</h2>
        <p className="note">No ground-truth labels exist for uploaded data, so precision and recall cannot be measured. Flags are suspicions to review, not verified fraud.</p>
      </>
    );
  }
  const rows = [
    ['Precision', pct(E.precision)], ['Recall', pct(E.recall)], ['F1 score', E.f1 == null ? 'n/a' : E.f1.toFixed(3)],
    ['False-positive rate', pct(E.falsePositiveRate, 2)], [`Precision at k (k = ${E.k})`, pct(E.precisionAtK)],
    ['PR-AUC (ranking by score)', E.prAuc == null ? 'n/a' : E.prAuc.toFixed(3)], ['Threshold', `score ${meta.threshold} or higher`],
    ['Full analysis runtime', `${meta.runtime ?? 'n/a'} s`],
  ];
  return (
    <>
      <h2>Detection quality<small>{E.records.toLocaleString('en-IN')} holdout rows after the training cutoff, {E.positives} injected positives</small></h2>
      <table><tbody>{rows.map(([k, v]) => <tr key={k}><td>{k}</td><td className="n">{v}</td></tr>)}</tbody></table>
      <p className="note">
        Measured on synthetic data with injected scenarios, where about 1% of holdout rows are positives. That is far above real fraud rates,
        so real precision would be lower. k uses the true positive count, so precision at k shows ranking quality, not a deployable setting.
        Benign lookalikes were flagged at {pct(E.benignFlagRate, 1)}.
      </p>
    </>
  );
}

export function ScenarioPanel({ evaluation: E }) {
  if (!E) return null;
  const C = E.confusion;
  return (
    <>
      <h2>Recall by injected scenario</h2>
      <div className="bars">
        {Object.entries(E.recallByScenario).map(([k, v]) => (
          // green when recall >= 60%, amber otherwise
          <Row key={k} label={SCEN[k] || k} width={Math.max(v * 100, 1)} value={pct(v, 0)} color={`var(--${v < 0.6 ? 'high' : 'ok'})`} />
        ))}
      </div>
      <h2 style={{ marginTop: 10 }}>Confusion matrix</h2>
      <table>
        <thead><tr><th /><th className="n">Flagged</th><th className="n">Not flagged</th></tr></thead>
        <tbody>
          <tr><td>Injected scenario</td><td className="n">{C.tp}</td><td className="n">{C.fn}</td></tr>
          <tr><td>Everything else</td><td className="n">{C.fp}</td><td className="n">{C.tn}</td></tr>
        </tbody>
      </table>
    </>
  );
}

/** "0.7312 +- 0.0104" style cell; n/a when a metric is undefined. */
const ms = (m, sd, k) => (m?.[k] == null ? 'n/a' : sd?.[k] == null ? m[k].toFixed(3) : `${m[k].toFixed(3)} \u00B1 ${sd[k].toFixed(3)}`);

/**
 * K-fold cross-validation of the ML component (see detection/crossval.py).
 * Each row is one fold: a fresh isolation forest is trained on the other folds and the risk score is
 * evaluated on this fold. The mean ± standard deviation row tells you how stable the metrics are.
 */
export function CrossValidationPanel({ cv }) {
  if (!cv) {
    return (
      <>
        <h2>Cross-validation</h2>
        <p className="note">No cross-validation for this run. It needs ground-truth labels (synthetic runs only) and must be switched on in the run bar.</p>
      </>
    );
  }
  const cols = [['precision', 'Precision'], ['recall', 'Recall'], ['f1', 'F1'], ['falsePositiveRate', 'FPR'], ['prAuc', 'PR-AUC'], ['prAucMlOnly', 'PR-AUC (forest only)']];
  const cell = (v, d = 3) => (v == null ? 'n/a' : v.toFixed(d));
  return (
    <>
      <h2>{cv.folds}-fold cross-validation<small>{cv.mode} folds, {cv.features.length} ML features, {cv.seconds} s</small></h2>
      <div className="tw">
        <table>
          <thead><tr><th>Fold</th><th className="n">Rows</th><th className="n">Positives</th>{cols.map(([k, l]) => <th className="n" key={k}>{l}</th>)}</tr></thead>
          <tbody>
            {cv.perFold.map((f) => (
              <tr key={f.fold} style={{ cursor: 'default' }}>
                <td>{f.fold}</td><td className="n">{f.n.toLocaleString('en-IN')}</td><td className="n">{f.positives}</td>
                {cols.map(([k]) => <td className="n" key={k}>{cell(f[k], k === 'falsePositiveRate' ? 4 : 3)}</td>)}
              </tr>
            ))}
            <tr style={{ cursor: 'default', fontWeight: 600 }}>
              <td colSpan="3">Mean ± std over folds</td>
              {cols.map(([k]) => <td className="n" key={k}>{ms(cv.mean, cv.std, k)}</td>)}
            </tr>
            <tr style={{ cursor: 'default' }}>
              <td colSpan="3">Pooled out-of-fold</td>
              {cols.map(([k]) => <td className="n" key={k}>{cell(cv.pooled[k], k === 'falsePositiveRate' ? 4 : 3)}</td>)}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="note">
        {cv.note} Rules have fixed thresholds, so only the isolation forest is re-trained per fold; "forest only" shows how good its ranking is on its own,
        which is the number that changes when you drop features. A small standard deviation means the result does not hinge on one lucky split.
      </p>
    </>
  );
}
