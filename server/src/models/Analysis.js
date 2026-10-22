/**
 * Analysis model - one document per detection RUN.
 *
 * An "analysis" is the result of sending a batch of transactions (generated synthetic data
 * or an uploaded CSV/JSON file) through the Python detection engine. It stores everything
 * the dashboard needs to draw its summary, timeline and evaluation panels. The individual
 * flagged transactions live in the separate `Case` collection (one document per case) so
 * that analysts can update status/notes on a case without rewriting this big document.
 */
const { Schema, model } = require('mongoose');

const AnalysisSchema = new Schema(
  {
    // Human friendly label shown in the run selector, e.g. "Synthetic 2,000 accounts (seed 42)"
    name: { type: String, required: true, trim: true, maxlength: 120 },
    // Where the transactions came from
    source: { type: String, enum: ['synthetic', 'upload'], required: true },
    // Original request parameters (generate options or uploaded file name) - for reproducibility
    params: { type: Schema.Types.Mixed, default: {} },
    // Effective detector configuration used for this run (defaults + overrides)
    config: { type: Schema.Types.Mixed, default: {} },

    // Headline numbers for the summary bar (see detection/report.py -> build_report().meta)
    meta: {
      records: Number, flagged: Number, critical: Number, high: Number,
      accounts: Number, accountsTotal: Number, days: Number, start: String,
      duplicates: Number, rejected: Number, threshold: Number, runtime: Number,
    },
    // Transactions per day and flagged per day, arrays index = day number since `meta.start`
    volume: { total: [Number], flagged: [Number] },
    // { burst: 49, cycle: 40, ... } how many flagged transactions each detector contributed to
    patterns: { type: Map, of: Number, default: {} },
    // Evaluation metrics against synthetic ground truth. `null` for uploaded files (no labels exist).
    evaluation: { type: Schema.Types.Mixed, default: null },
    // K-fold cross-validation of the ML component (synthetic runs only, `null` otherwise): per-fold metrics,
    // mean/std over folds, pooled out-of-fold metrics, and the feature list that was used.
    crossValidation: { type: Schema.Types.Mixed, default: null },
    // Data-quality report from the engine (rows in, rejected, duplicates removed, ...)
    quality: { type: Schema.Types.Mixed, default: {} },
    // A few rejected rows with the reason, so the UI can show *why* rows were refused
    rejectedRows: { type: [Schema.Types.Mixed], default: [] },
    // Free-text warnings from the engine (e.g. "ML component disabled")
    warnings: { type: [String], default: [] },
    // Seconds spent per pipeline stage
    timings: { type: Schema.Types.Mixed, default: {} },
    // How many Case documents were stored for this run (<= requested limit)
    caseCount: { type: Number, default: 0 },
  },
  {
    timestamps: true, // adds createdAt / updatedAt automatically
    // Strip Mongo internals when converting to JSON for the API response
    toJSON: {
      versionKey: false,
      transform: (_doc, ret) => {
        // Map -> plain object so JSON.stringify prints { burst: 49 } instead of {}
        if (ret.patterns instanceof Map) ret.patterns = Object.fromEntries(ret.patterns);
        return ret;
      },
    },
  }
);

// Newest runs first in the run selector
AnalysisSchema.index({ createdAt: -1 });

module.exports = { Analysis: model('Analysis', AnalysisSchema) };
