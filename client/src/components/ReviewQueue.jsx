/**
 * ReviewQueue.jsx - the analyst's worklist: flagged transactions, riskiest first.
 *
 * Filtering, searching and paging are done by the SERVER (GET /api/analyses/:id/cases), so
 * this scales to thousands of cases. Changing any filter resets to page 1.
 */
import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import useDebounce from '../hooks/useDebounce.js';
import { PAT, STATUSES, inr, when, catClass } from '../lib/format.js';

const PAGE_SIZE = 25;

export default function ReviewQueue({ analysisId, selectedId, onSelect, reloadKey }) {
  const [category, setCategory] = useState('');
  const [pattern, setPattern] = useState('');
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const q = useDebounce(search);          // only query the server after typing pauses
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ items: [], total: 0, pages: 1 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Any filter change (or a different run) => back to page 1
  useEffect(() => { setPage(1); }, [analysisId, category, pattern, status, q]);

  // Fetch the page whenever the query changes
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.listCases(analysisId, { category, pattern, status, q, page, limit: PAGE_SIZE })
      .then((d) => {
        if (cancelled) return;
        setData(d); setError('');
        // Auto-select the first case if nothing (or something no longer visible) is selected
        if (d.items.length && !d.items.some((c) => c._id === selectedId)) onSelect(d.items[0]._id);
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
    // selectedId/onSelect are intentionally omitted: we only refetch when the QUERY changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisId, category, pattern, status, q, page, reloadKey]);

  return (
    <section>
      <h2>Review queue<small>{loading ? 'loading…' : `${data.total} cases, highest risk first`}</small></h2>
      <div className="filters">
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Risk level">
          <option value="">All risk levels</option><option>critical</option><option>high</option>
        </select>
        <select value={pattern} onChange={(e) => setPattern(e.target.value)} aria-label="Pattern">
          <option value="">All patterns</option>
          {Object.entries(PAT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Case status">
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option key={s}>{s}</option>)}
        </select>
        <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search account or transaction ID" aria-label="Search account or transaction ID" />
      </div>
      {error && <p className="banner">{error}</p>}
      <div className="tw qwrap">
        <table>
          <thead><tr><th className="n">Risk</th><th>Time</th><th>Sender and receiver</th><th className="n">Amount</th><th>Pattern</th><th>Status</th></tr></thead>
          <tbody>
            {!data.items.length && <tr><td colSpan="6" className="empty">{loading ? 'Loading…' : 'No cases match these filters. Clear the search or choose another pattern.'}</td></tr>}
            {data.items.map((c) => {
              const pick = () => onSelect(c._id);
              return (
                <tr key={c._id} tabIndex={0} aria-selected={c._id === selectedId} onClick={pick}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } }}>
                  <td className={`n score ${catClass(c.riskCategory)}`}>{c.riskScore.toFixed(0)}</td>
                  <td className="m">{when(c.timestamp)}</td>
                  <td>{c.sender}<br /><span className="m">to {c.receiver}</span></td>
                  <td className="n">{inr(c.amount)}</td>
                  <td>{c.evidence.map((e) => PAT[e.pattern]).join(', ')}</td>
                  <td className="m">{c.status}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {data.pages > 1 && (
        <div className="pager">
          <button onClick={() => setPage((p) => p - 1)} disabled={page <= 1}>Previous</button>
          <span>Page {page} of {data.pages}</span>
          <button onClick={() => setPage((p) => p + 1)} disabled={page >= data.pages}>Next</button>
        </div>
      )}
    </section>
  );
}
