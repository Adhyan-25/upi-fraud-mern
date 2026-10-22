/**
 * Case model - one document per FLAGGED transaction (a "case" an analyst can review).
 *
 * The detection fields (risk score, evidence, graph) are written once when the analysis is
 * created. The workflow fields (status, notes, history) are edited later by analysts through
 * PATCH /api/cases/:id - this is the part that used to be stored only in the browser's
 * localStorage in the original static dashboard and now lives in MongoDB.
 */
const { Schema, model } = require('mongoose');

// Allowed workflow states, in the order they appear in the dropdown.
const STATUSES = ['Open', 'Investigating', 'Escalated', 'Resolved'];
// The six detectors implemented by the Python engine (irregular_ip = IP shared by many accounts / brand-new IP).
const PATTERNS = ['cycle', 'rapid_onward', 'burst', 'split', 'unusual_behavior', 'irregular_ip'];

// One "reason" the transaction was flagged (sub-document, no own _id needed)
const EvidenceSchema = new Schema(
  {
    pattern: { type: String, enum: PATTERNS, required: true },
    weight: { type: Number, required: true }, // points this pattern added to the risk score
    text: { type: String, required: true },   // plain-English explanation
  },
  { _id: false }
);

// One edge of the small money-flow graph drawn next to the case
const EdgeSchema = new Schema(
  {
    from: String, to: String, amount: Number,
    flagged: Boolean, // is this neighbouring transfer flagged too?
    focus: Boolean,   // is this edge the case's own transaction?
  },
  { _id: false }
);

// One entry in the audit trail of status changes
const HistorySchema = new Schema(
  { from: String, to: String, at: { type: Date, default: Date.now } },
  { _id: false }
);

const CaseSchema = new Schema(
  {
    // Which run produced this case (so deleting a run can delete its cases)
    analysis: { type: Schema.Types.ObjectId, ref: 'Analysis', required: true, index: true },
    txId: { type: String, required: true },
    timestamp: { type: Date, required: true }, // time of the transaction itself
    sender: { type: String, required: true },
    receiver: { type: String, required: true },
    amount: { type: Number, required: true },  // rupees
    riskScore: { type: Number, required: true, min: 0, max: 100 }, // heuristic ranking score, NOT a probability
    riskCategory: { type: String, enum: ['low', 'medium', 'high', 'critical'], required: true },
    mlPercentile: { type: Number, default: 0 }, // IsolationForest anomaly percentile, 0..1
    // Context columns of the flagged transaction (null when the uploaded data does not have them)
    ip: { type: String, default: null },      // IPv4 address the transfer came from
    device: { type: String, default: null },  // android | ios | web
    channel: { type: String, default: null }, // pay | qr | collect
    evidence: { type: [EvidenceSchema], default: [] },
    graph: { type: [EdgeSchema], default: [] },

    // ---- analyst workflow ----
    status: { type: String, enum: STATUSES, default: 'Open' },
    notes: { type: String, default: '', maxlength: 5000 },
    history: { type: [HistorySchema], default: [] },
  },
  {
    timestamps: true,
    toJSON: { versionKey: false },
  }
);

// A transaction id appears once per analysis
CaseSchema.index({ analysis: 1, txId: 1 }, { unique: true });
// Default queue ordering: highest risk first within one analysis
CaseSchema.index({ analysis: 1, riskScore: -1 });
// Pattern filter in the review queue
CaseSchema.index({ analysis: 1, 'evidence.pattern': 1 });

const Case = model('Case', CaseSchema);

// Named exports: the model plus the enums (routes/validators reuse them so the lists never drift apart)
module.exports = { Case, STATUSES, PATTERNS };
