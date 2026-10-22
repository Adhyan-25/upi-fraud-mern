/**
 * RunBar.jsx - the control strip for analysis RUNS.
 *
 *  - choose an existing run from a dropdown, or delete it,
 *  - generate a new synthetic run: dataset size (up to ONE MILLION transactions), seed, optional account count,
 *  - 5-fold cross-validation switch (folds + split mode),
 *  - "drop features": ML features to switch off - the hook for feature-selection experiments,
 *  - upload a CSV or JSON file of transactions.
 *
 * It holds only the form state; the actual API calls are performed by App (props `onCreate`,
 * `onUpload`, `onDelete`), which keeps this component purely presentational.
 */
import { useRef, useState } from 'react';
import { SIZE_PRESETS, FEATURE_GROUPS, parseList } from '../lib/format.js';

const ALL_FEATURES = Object.values(FEATURE_GROUPS).flat();

export default function RunBar({ runs, analysisId, onSelect, onCreate, onUpload, onDelete, busy }) {
  // Controlled inputs: React state is the single source of truth for each field
  const [nTransactions, setNTransactions] = useState(20000);
  const [nAccounts, setNAccounts] = useState('');      // empty = automatic (about 1 account per 10 transactions)
  const [seed, setSeed] = useState(42);
  const [cv, setCv] = useState(true);                  // run K-fold cross-validation?
  const [cvFolds, setCvFolds] = useState(5);
  const [cvMode, setCvMode] = useState('stratified');
  const [drop, setDrop] = useState('');                // comma separated feature names to disable
  const fileRef = useRef(null);                        // direct handle to the hidden <input type="file">

  const dropList = parseList(drop);
  const unknown = dropList.filter((f) => !ALL_FEATURES.includes(f)); // shown as a warning; the server also validates

  const submitSynthetic = (e) => {
    e.preventDefault(); // stop the browser's default full-page form submit
    const opts = { nTransactions: Number(nTransactions), seed: Number(seed), cv, cvFolds: Number(cvFolds), cvMode };
    if (nAccounts !== '') opts.nAccounts = Number(nAccounts);
    if (dropList.length) opts.disabledFeatures = dropList;
    onCreate(opts);
  };

  const submitFile = (e) => {
    const file = e.target.files[0];
    if (file) onUpload(file);
    e.target.value = ''; // reset so choosing the same file twice still fires onChange
  };

  const big = Number(nTransactions) >= 500000;

  return (
    <div className="runbar">
      <div className="grp">
        <label htmlFor="run">Run</label>
        <select id="run" value={analysisId || ''} onChange={(e) => onSelect(e.target.value)} disabled={!runs.length || busy}>
          {!runs.length && <option value="">none yet</option>}
          {runs.map((r) => (
            <option key={r._id} value={r._id}>
              {r.name} - {r.meta?.flagged ?? 0} flagged ({new Date(r.createdAt).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' })})
            </option>
          ))}
        </select>
        <button className="danger" onClick={() => window.confirm('Delete this run and all its cases?') && onDelete()} disabled={!analysisId || busy}>
          Delete
        </button>
      </div>

      <form className="grp" onSubmit={submitSynthetic}>
        <label htmlFor="nt">Synthetic data</label>
        <select aria-label="Size preset" value={SIZE_PRESETS.some((p) => p.n === Number(nTransactions)) ? nTransactions : ''} onChange={(e) => e.target.value && setNTransactions(Number(e.target.value))}>
          {!SIZE_PRESETS.some((p) => p.n === Number(nTransactions)) && <option value="">custom</option>}
          {SIZE_PRESETS.map((p) => <option key={p.n} value={p.n}>{p.label}</option>)}
        </select>
        <input id="nt" type="number" min="100" max="1000000" value={nTransactions} onChange={(e) => setNTransactions(e.target.value)} title="Background transactions (100 to 1,000,000)" aria-label="Transactions" />
        <input type="number" min="60" max="200000" value={nAccounts} placeholder="accounts (auto)" onChange={(e) => setNAccounts(e.target.value)} title="Number of accounts (empty = about one per 10 transactions)" aria-label="Accounts" style={{ flex: "0 1 130px" }} />
        <input type="number" value={seed} onChange={(e) => setSeed(e.target.value)} title="Random seed: the same seed gives identical data" aria-label="Seed" style={{ flex: '0 1 70px' }} />
        <label title="Fit the isolation forest on K-1 folds and test on the held-out fold, K times">
          <input type="checkbox" checked={cv} onChange={(e) => setCv(e.target.checked)} style={{ flex: 'none' }} /> {cvFolds}-fold CV
        </label>
        {cv && (
          <>
            <input type="number" min="2" max="10" value={cvFolds} onChange={(e) => setCvFolds(e.target.value)} aria-label="Number of folds" title="Folds (2-10)" style={{ flex: '0 1 56px' }} />
            <select value={cvMode} onChange={(e) => setCvMode(e.target.value)} aria-label="Cross-validation split mode" title="stratified: random folds with equal fraud share. grouped: a scenario never straddles train/test. time: train on the past, test on the next slice.">
              <option value="stratified">stratified</option><option value="grouped">grouped</option><option value="time">time-ordered</option>
            </select>
          </>
        )}
        <input type="text" value={drop} onChange={(e) => setDrop(e.target.value)} placeholder="drop features: note_len, app_major" list="feature-names" aria-label="Features to drop" title="Comma separated ML feature names to switch off (feature selection)" style={{ flex: '1 1 200px' }} />
        <datalist id="feature-names">{ALL_FEATURES.map((f) => <option key={f} value={f} />)}</datalist>
        <button className="primary" type="submit" disabled={busy || unknown.length > 0}>{busy ? 'Running…' : 'Generate & analyse'}</button>
        {unknown.length > 0 && <span className="crit">unknown feature: {unknown.join(', ')}</span>}
        {big && !busy && <span className="m">1M rows: about 1-2 minutes{cv ? ' incl. cross-validation' : ''}, needs ~3 GB RAM</span>}
      </form>

      <div className="grp">
        {/* The real file input is hidden; this button just opens its picker */}
        <input ref={fileRef} type="file" accept=".csv,.json,text/csv,application/json" onChange={submitFile} hidden />
        <button onClick={() => fileRef.current.click()} disabled={busy} title="Required columns: tx_id,timestamp,sender,receiver,amount,status. Optional: ip_address, device_type, app_version, channel, sender_bank, receiver_bank, sender_state, receiver_state, account_age_days, kyc_level, txn_note_len, network_type">
          Upload CSV / JSON…
        </button>
      </div>
    </div>
  );
}
