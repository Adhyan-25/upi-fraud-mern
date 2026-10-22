/**
 * SummaryBar.jsx - one sentence of headline numbers plus the workflow status counts.
 */
import { pct } from '../lib/format.js';

export default function SummaryBar({ analysis }) {
  const { meta, statusCounts, quality } = analysis;
  const n = (v) => Number(v || 0).toLocaleString('en-IN');
  return (
    <p className="summary">
      <b>{n(meta.records)}</b> transactions analysed. <b>{n(meta.flagged)}</b> flagged ({pct(meta.records ? meta.flagged / meta.records : 0, 2)}):{' '}
      <b className="crit">{n(meta.critical)}</b> critical and <b className="high">{n(meta.high)}</b> high. <b>{n(meta.accounts)}</b> accounts involved.{' '}
      {n(meta.duplicates)} duplicate rows removed, {n(quality?.failed_excluded)} failed transfers excluded and {n(meta.rejected)} rejected before scoring.{' '}
      {statusCounts && (
        <span className="m">
          Cases: {Object.entries(statusCounts).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(', ')}.{' '}
        </span>
      )}
      Scores rank suspicion for review; they are not fraud probabilities and nothing here is a verified finding.
    </p>
  );
}
