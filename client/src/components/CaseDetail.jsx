/**
 * CaseDetail.jsx - everything about ONE flagged transaction, plus the analyst's workflow controls.
 *
 *  - risk score + "model suspicion, not verified fraud" reminder,
 *  - plain-English reasons (evidence) the transaction was flagged,
 *  - money-flow graph,
 *  - status dropdown and free-text notes, both SAVED TO MONGODB through PATCH /api/cases/:id
 *    (the original static dashboard kept these in the browser's localStorage only).
 *
 * Notes are auto-saved 700 ms after the analyst stops typing; status is saved immediately.
 */
import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import MoneyGraph from './MoneyGraph.jsx';
import { PAT, STATUSES, inr, when, pct, catClass } from '../lib/format.js';

export default function CaseDetail({ caseId, onChanged }) {
  const [c, setC] = useState(null);          // the loaded case
  const [notes, setNotes] = useState('');    // local copy of the textarea (saved with a delay)
  const [focus, setFocus] = useState(null);  // account highlighted in the graph
  const [saved, setSaved] = useState('');    // small status message under the controls
  const [error, setError] = useState('');
  const timer = useRef(null);                // pending debounced save

  // Load the case when the selection changes
  useEffect(() => {
    if (!caseId) { setC(null); return undefined; }
    let cancelled = false;
    clearTimeout(timer.current);             // drop a pending save of the PREVIOUS case's notes
    setError(''); setSaved(''); setFocus(null);
    api.getCase(caseId)
      .then((d) => { if (!cancelled) { setC(d); setNotes(d.notes || ''); } })
      .catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [caseId]);

  // Clear any pending timer when the component unmounts
  useEffect(() => () => clearTimeout(timer.current), []);

  /** Send a partial update to the server and refresh the local copy with the response. */
  const save = async (patch, message) => {
    try {
      const updated = await api.updateCase(caseId, patch);
      setC(updated); setSaved(message); setError('');
      return updated;
    } catch (e) {
      setError(e.message); setSaved('');
      return null;
    }
  };

  const onStatus = async (e) => {
    const updated = await save({ status: e.target.value }, 'Status saved.');
    if (updated) onChanged(); // tell the parent so the queue and summary counts refresh
  };

  const onNotes = (e) => {
    const value = e.target.value;
    setNotes(value);
    setSaved('Saving…');
    clearTimeout(timer.current);
    // Capture the id now: if the user switches case before the timer fires we must NOT save into the new case
    const id = caseId;
    timer.current = setTimeout(() => { if (id === caseId) save({ notes: value }, 'Notes saved.'); }, 700);
  };

  if (!caseId) return <section><p className="empty">Select a case from the review queue.</p></section>;
  if (error && !c) return <section><p className="banner">{error}</p></section>;
  if (!c) return <section><p className="empty">Loading case…</p></section>;

  return (
    <div aria-live="polite">
      <section>
        <h2>{c.txId}<small>{when(c.timestamp)}</small></h2>
        <div className="row2">
          <span className={`score ${catClass(c.riskCategory)}`} style={{ fontSize: 18 }}>{c.riskScore.toFixed(1)}</span>
          <span>{c.riskCategory} risk</span>
          <span className="chip">Model suspicion, not verified fraud</span>
          <span className="m">anomaly rank {pct(c.mlPercentile, 0)} of training rows</span>
        </div>
        <p style={{ margin: '6px 0 0' }}>{c.sender} sent {inr(c.amount)} to {c.receiver}.</p>
        {(c.ip || c.device || c.channel) && (
          <div className="row2" style={{ marginTop: 6 }}>
            {c.ip && <span className="chip" title="IP address the transfer came from">IP {c.ip}</span>}
            {c.device && <span className="chip" title="Device type">{c.device}</span>}
            {c.channel && <span className="chip" title="UPI channel">{c.channel}</span>}
          </div>
        )}
      </section>

      <section>
        <h2>Why it was flagged</h2>
        {c.evidence.map((e) => (
          <p className="ev" key={e.pattern}>
            <b>{PAT[e.pattern]}</b> adds {e.weight} points. {e.text[0].toUpperCase() + e.text.slice(1).replace(/'/g, '’')}
          </p>
        ))}
      </section>

      <section>
        <h2>Money flow nearby
          <small>transfers within 3 hours, thick line is this transaction{focus ? `, showing ${focus}` : ', select an account to isolate its transfers'}</small>
        </h2>
        <MoneyGraph caseData={c} focusNode={focus} onFocus={setFocus} />
      </section>

      <section>
        <h2>Investigation</h2>
        <div className="row2">
          <label>Status{' '}
            <select value={c.status} onChange={onStatus} aria-label="Case status">
              {STATUSES.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <span className="m">{error ? <span className="crit">{error}</span> : saved || 'Status and notes are stored in MongoDB.'}</span>
        </div>
        <textarea value={notes} onChange={onNotes} placeholder="Notes for this case" aria-label="Analyst notes" maxLength={5000} />
        {c.history.length > 0 && (
          <ul className="history">
            {c.history.map((h, i) => <li key={i}>{new Date(h.at).toLocaleString('en-IN')}: {h.from} → {h.to}</li>)}
          </ul>
        )}
      </section>
    </div>
  );
}
