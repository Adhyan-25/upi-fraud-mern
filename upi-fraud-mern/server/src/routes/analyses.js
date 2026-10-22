/**
 * routes/analyses.js - everything about detection RUNS.
 *
 *   POST   /api/analyses              run on freshly generated synthetic data   (JSON body)
 *   POST   /api/analyses/upload       run on an uploaded CSV / JSON file        (multipart/form-data)
 *   GET    /api/analyses              list runs, newest first
 *   GET    /api/analyses/:id          one run (+ how many cases are in each workflow status)
 *   GET    /api/analyses/:id/cases    paginated, filterable review queue for the run
 *   DELETE /api/analyses/:id          delete the run and all its cases
 */
const { Router } = require('express');
const multer = require('multer');
const { z } = require('zod');
const mongoose = require('mongoose');
const { Analysis } = require('../models/Analysis');
const { Case, STATUSES, PATTERNS } = require('../models/Case');
const { runAndStore } = require('../services/analysisService');
const { parseTransactions } = require('../utils/parseTransactions');
const { validate, objectIdParam } = require('../middleware/validate');
const asyncHandler = require('../utils/asyncHandler');
const HttpError = require('../utils/httpError');

// ---------------------------------------------------------------- validation schemas
/** Body of POST /api/analyses */
const generateBody = z.object({
  name: z.string().trim().min(1).max(120).optional(),
  // Same limits as the Python service so errors are caught early with a friendly message.
  // nAccounts is optional: when omitted the engine uses ~1 account per 10 transactions (100,000 accounts for 1,000,000 transactions).
  nAccounts: z.number().int().min(60).max(200000).optional(),
  nTransactions: z.number().int().min(100).max(1000000).default(10000), // up to ONE MILLION
  seed: z.number().int().default(42),
  limit: z.number().int().min(1).max(1000).default(100), // max cases to store
  config: z.record(z.any()).optional(), // detector overrides; Python validates the details
  // Feature selection shortcut: names of ML features to switch OFF (merged into config.disabled_features)
  disabledFeatures: z.array(z.string().max(40)).max(40).optional(),
  // K-fold cross-validation of the ML component (needs the ground-truth labels of synthetic data)
  cv: z.boolean().default(true),
  cvFolds: z.number().int().min(2).max(10).default(5),
  cvMode: z.enum(['stratified', 'grouped', 'time']).default('stratified'),
});

/** Query string of GET /api/analyses/:id/cases. z.coerce turns "50" into 50. */
const casesQuery = z.object({
  category: z.enum(['low', 'medium', 'high', 'critical']).optional(),
  pattern: z.enum(PATTERNS).optional(),
  status: z.enum(STATUSES).optional(),
  q: z.string().trim().max(80).optional(), // search text: tx id or account
  page: z.coerce.number().int().min(1).default(1),
  limit: z.coerce.number().int().min(1).max(200).default(50),
});

/** Escape regex metacharacters so user text is matched literally (prevents ReDoS / regex injection). */
const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

module.exports = (deps) => {
  const router = Router();
  // multer parses multipart uploads. memoryStorage keeps the file in RAM (fine for <= MAX_UPLOAD_MB).
  const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: deps.config.maxUploadBytes, files: 1 } });

  // ------------------------------------------------------------ POST /api/analyses
  router.post(
    '/',
    validate('body', generateBody),
    asyncHandler(async (req, res) => {
      const b = req.body;
      // merge the convenience field `disabledFeatures` into the detector config
      const config = b.disabledFeatures ? { ...(b.config || {}), disabled_features: b.disabledFeatures } : b.config;
      const analysis = await runAndStore(deps, {
        name: b.name || `Synthetic ${b.nTransactions.toLocaleString('en-IN')} transactions (seed ${b.seed})`,
        source: 'synthetic',
        params: { nAccounts: b.nAccounts ?? null, nTransactions: b.nTransactions, seed: b.seed, cv: b.cv, cvFolds: b.cvFolds, cvMode: b.cvMode },
        // translate camelCase API names into the snake_case the Python service expects
        request: {
          generate: { n_accounts: b.nAccounts, n_transactions: b.nTransactions, seed: b.seed },
          config,
          limit: b.limit,
          cross_validation: { enabled: b.cv, folds: b.cvFolds, mode: b.cvMode },
        },
      });
      res.status(201).json(analysis);
    })
  );

  // ------------------------------------------------------------ POST /api/analyses/upload
  router.post(
    '/upload',
    upload.single('file'), // field name must be "file"
    asyncHandler(async (req, res) => {
      if (!req.file) throw new HttpError(400, 'Attach a CSV or JSON file in the "file" field');
      // Extra form fields arrive as strings; parse and validate them by hand
      let config;
      if (req.body.config) {
        try {
          config = JSON.parse(req.body.config);
        } catch {
          throw new HttpError(400, 'config must be valid JSON');
        }
      }
      const limit = req.body.limit === undefined ? 100 : Number(req.body.limit);
      if (!Number.isInteger(limit) || limit < 1 || limit > 1000) throw new HttpError(400, 'limit must be an integer in [1, 1000]');
      const transactions = parseTransactions(req.file.buffer, req.file.originalname);
      const name = (req.body.name || '').trim().slice(0, 120) || `Upload: ${req.file.originalname}`;

      const analysis = await runAndStore(deps, {
        name,
        source: 'upload',
        params: { filename: req.file.originalname, bytes: req.file.size, rows: transactions.length },
        request: { transactions, config, limit },
      });
      res.status(201).json(analysis);
    })
  );

  // ------------------------------------------------------------ GET /api/analyses
  router.get(
    '/',
    asyncHandler(async (_req, res) => {
      // Only the light fields for the dropdown; the big arrays stay in the detail endpoint
      const list = await Analysis.find({}, 'name source meta caseCount createdAt').sort({ createdAt: -1 }).limit(100);
      res.json(list);
    })
  );

  // ------------------------------------------------------------ GET /api/analyses/:id
  router.get(
    '/:id',
    objectIdParam('id'),
    asyncHandler(async (req, res) => {
      const analysis = await Analysis.findById(req.params.id);
      if (!analysis) throw new HttpError(404, 'Analysis not found');
      // How many cases are in each workflow status? (one aggregation, grouped by status)
      const rows = await Case.aggregate([
        { $match: { analysis: new mongoose.Types.ObjectId(req.params.id) } },
        { $group: { _id: '$status', n: { $sum: 1 } } },
      ]);
      const statusCounts = Object.fromEntries(STATUSES.map((s) => [s, 0]));
      rows.forEach((r) => { statusCounts[r._id] = r.n; });
      res.json({ ...analysis.toJSON(), statusCounts });
    })
  );

  // ------------------------------------------------------------ GET /api/analyses/:id/cases
  router.get(
    '/:id/cases',
    objectIdParam('id'),
    validate('query', casesQuery),
    asyncHandler(async (req, res) => {
      const { category, pattern, status, q, page, limit } = req.query;
      // Build the Mongo filter step by step; each query-string option narrows it
      const filter = { analysis: req.params.id };
      if (category) filter.riskCategory = category;
      if (pattern) filter['evidence.pattern'] = pattern;
      if (status) filter.status = status;
      if (q) {
        const rx = new RegExp(escapeRegex(q), 'i'); // case-insensitive literal match
        filter.$or = [{ txId: rx }, { sender: rx }, { receiver: rx }];
      }
      const [items, total] = await Promise.all([
        // List view needs only a few fields; `graph` and `notes` are fetched on demand via /api/cases/:id
        Case.find(filter, 'txId timestamp sender receiver amount riskScore riskCategory evidence.pattern status')
          .sort({ riskScore: -1, _id: 1 }) // _id as tiebreaker => stable pagination
          .skip((page - 1) * limit)
          .limit(limit),
        Case.countDocuments(filter),
      ]);
      res.json({ items, total, page, pages: Math.max(1, Math.ceil(total / limit)), limit });
    })
  );

  // ------------------------------------------------------------ DELETE /api/analyses/:id
  router.delete(
    '/:id',
    objectIdParam('id'),
    asyncHandler(async (req, res) => {
      const analysis = await Analysis.findByIdAndDelete(req.params.id);
      if (!analysis) throw new HttpError(404, 'Analysis not found');
      const { deletedCount } = await Case.deleteMany({ analysis: req.params.id }); // cascade
      res.json({ deleted: true, casesDeleted: deletedCount });
    })
  );

  return router;
};
