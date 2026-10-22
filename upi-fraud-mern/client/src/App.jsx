/**
 * App.jsx - the root component. It owns the "which analysis run am I looking at?" state and
 * lays out the dashboard.
 *
 * Data flow (top-down, the usual React way):
 *
 *   App ── runs[] (list of analyses)            ──► RunBar (select / create / upload / delete)
 *       ├─ analysis (details of the selected run) ──► SummaryBar, VolumeChart, PatternBars, EvaluationPanel
 *       └─ selectedCaseId                         ──► ReviewQueue (pick) and CaseDetail (show/edit)
 *
 * All persistence happens on the server (MongoDB) through lib/api.js.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from './lib/api.js';
import Header from './components/Header.jsx';
import RunBar from './components/RunBar.jsx';
import SummaryBar from './components/SummaryBar.jsx';
import VolumeChart from './components/VolumeChart.jsx';
import ReviewQueue from './components/ReviewQueue.jsx';
import CaseDetail from './components/CaseDetail.jsx';
import { PatternBars, EvaluationPanel, ScenarioPanel, CrossValidationPanel } from './components/Panels.jsx';

export default function App() {
  const [runs, setRuns] = useState([]);               // light list for the selector
  const [analysisId, setAnalysisId] = useState(null); // currently selected run
  const [analysis, setAnalysis] = useState(null);     // full details of that run
  const [selectedCaseId, setSelectedCaseId] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);            // true while a run is being created/deleted
  const [reload, setReload] = useState(0);            // bump to refetch the analysis (e.g. status counts changed)

  /** Refresh the list of runs; optionally select a specific one. */
  const refreshRuns = useCallback(async (selectId) => {
    const list = await api.listAnalyses();
    setRuns(list);
    // Keep the current selection if it still exists, otherwise fall back to the newest run
    setAnalysisId((cur) => selectId || (list.some((r) => r._id === cur) ? cur : list[0]?._id || null));
  }, []);

  // First load: fetch the runs
  useEffect(() => {
    refreshRuns().catch((e) => setError(e.message));
  }, [refreshRuns]);

  // Whenever the selected run changes (or `reload` is bumped) load its details
  useEffect(() => {
    if (!analysisId) { setAnalysis(null); return undefined; }
    let cancelled = false; // ignore the response if the user switched runs meanwhile (avoids race conditions)
    api.getAnalysis(analysisId)
      .then((a) => { if (!cancelled) setAnalysis(a); })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [analysisId, reload]);

  // Selecting a different run clears the selected case
  useEffect(() => { setSelectedCaseId(null); }, [analysisId]);

  /** Wrap an async action with busy/error handling so RunBar buttons stay simple. */
  const runAction = async (fn) => {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const onCreate = (opts) => runAction(async () => { const a = await api.createSynthetic(opts); await refreshRuns(a._id); });
  const onUpload = (file, name) => runAction(async () => { const a = await api.uploadFile(file, name); await refreshRuns(a._id); });
  const onDelete = () => runAction(async () => { await api.deleteAnalysis(analysisId); setAnalysisId(null); await refreshRuns(); });

  return (
    <>
      <Header analysis={analysis} />
      <RunBar runs={runs} analysisId={analysisId} onSelect={setAnalysisId} onCreate={onCreate} onUpload={onUpload} onDelete={onDelete} busy={busy} />
      {error && <div className="banner" role="alert">{error}</div>}
      {analysis?.warnings?.map((w) => <div className="warn" key={w}>{w}</div>)}

      {!analysis ? (
        <p className="summary">
          {busy ? 'Running detection… this can take a few seconds.' : 'No analysis yet. Generate synthetic data or upload a CSV above to get started.'}
        </p>
      ) : (
        <>
          <SummaryBar analysis={analysis} />
          <main>
            <div className="col">
              <section>
                <h2>Transactions and flags by day<small>hover a bar for the day’s counts</small></h2>
                <VolumeChart volume={analysis.volume} start={analysis.meta.start} />
              </section>
              <ReviewQueue analysisId={analysis._id} selectedId={selectedCaseId} onSelect={setSelectedCaseId} reloadKey={reload} />
            </div>
            <div className="col">
              {/* After a status change, bump `reload` so the status counts in the summary refresh */}
              <CaseDetail caseId={selectedCaseId} onChanged={() => setReload((n) => n + 1)} />
            </div>
          </main>
          <div className="grid3">
            <section><PatternBars patterns={analysis.patterns} /></section>
            <section><EvaluationPanel evaluation={analysis.evaluation} meta={analysis.meta} /></section>
            <section><ScenarioPanel evaluation={analysis.evaluation} /></section>
          </div>
          {analysis.source === 'synthetic' && <section><CrossValidationPanel cv={analysis.crossValidation} /></section>}
        </>
      )}
    </>
  );
}
