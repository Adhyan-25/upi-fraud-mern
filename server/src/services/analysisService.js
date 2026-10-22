/**
 * analysisService.js - the business logic of "run a detection and store the result".
 *
 * Routes stay thin (parse + validate the request, call this, send the response). This
 * service does the real work:
 *   1. call the detection service,
 *   2. map its snake_case payload to our Mongoose documents,
 *   3. save one Analysis and (in bulk) its Case documents.
 */
const { Analysis } = require('../models/Analysis');
const { Case } = require('../models/Case');

/**
 * Run a detection and persist the outcome.
 *
 * @param {object} deps       { detection } - the detection client (injected for testability)
 * @param {object} opts
 * @param {string} opts.name        label for the run
 * @param {'synthetic'|'upload'} opts.source
 * @param {object} opts.params      what produced the data (stored for reproducibility)
 * @param {object} opts.request     body forwarded to Flask: { transactions | generate, config?, limit? }
 * @returns {Promise<Analysis>} the saved Analysis document
 */
async function runAndStore({ detection }, { name, source, params, request }) {
  // 1) heavy lifting happens in Python
  const out = await detection.analyze(request);
  const d = out.dashboard; // presentation-shaped part of the response (see detection/report.py)

  // 2) save the run itself
  const analysis = await Analysis.create({
    name,
    source,
    params,
    config: out.config,
    meta: {
      records: d.meta.records, flagged: d.meta.flagged, critical: d.meta.critical, high: d.meta.high,
      accounts: d.meta.accounts, accountsTotal: d.meta.accounts_total, days: d.meta.days, start: d.meta.start,
      duplicates: d.meta.duplicates, rejected: d.meta.rejected, threshold: d.meta.threshold, runtime: d.meta.runtime,
    },
    volume: d.volume,
    patterns: d.patterns,
    evaluation: d.evaluation,
    crossValidation: d.crossValidation,
    quality: out.quality,
    rejectedRows: out.rejected_rows,
    warnings: out.warnings,
    timings: out.timings,
    caseCount: d.cases.length,
  });

  // 3) save the cases. insertMany is one round trip instead of N, and `ordered:false`
  //    lets MongoDB continue past a single bad document.
  if (d.cases.length) {
    await Case.insertMany(
      d.cases.map((c) => ({ ...c, analysis: analysis._id, timestamp: new Date(c.timestamp) })),
      { ordered: false }
    );
  }
  return analysis;
}

module.exports = { runAndStore };
