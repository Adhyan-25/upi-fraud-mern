/**
 * routes/cases.js - a single case (one flagged transaction).
 *
 *   GET   /api/cases/:id   full case: evidence, money-flow graph, status, notes, history
 *   PATCH /api/cases/:id   update the analyst's workflow fields: { status?, notes? }
 *
 * Only `status` and `notes` can be changed. Detection output (risk score, evidence...) is
 * immutable on purpose: an analyst's opinion must never silently rewrite the model's output.
 */
const { Router } = require('express');
const { z } = require('zod');
const { Case, STATUSES } = require('../models/Case');
const { validate, objectIdParam } = require('../middleware/validate');
const asyncHandler = require('../utils/asyncHandler');
const HttpError = require('../utils/httpError');

// `.strict()` rejects unknown keys, so { riskScore: 0 } is a 400 rather than being ignored
const patchBody = z
  .object({
    status: z.enum(STATUSES).optional(),
    notes: z.string().max(5000).optional(),
  })
  .strict()
  .refine((b) => b.status !== undefined || b.notes !== undefined, { message: 'provide status and/or notes' });

module.exports = () => {
  const router = Router();

  router.get(
    '/:id',
    objectIdParam('id'),
    asyncHandler(async (req, res) => {
      const c = await Case.findById(req.params.id);
      if (!c) throw new HttpError(404, 'Case not found');
      res.json(c);
    })
  );

  router.patch(
    '/:id',
    objectIdParam('id'),
    validate('body', patchBody),
    asyncHandler(async (req, res) => {
      const c = await Case.findById(req.params.id);
      if (!c) throw new HttpError(404, 'Case not found');
      const { status, notes } = req.body;
      // Append to the audit trail only when the status actually changes
      if (status !== undefined && status !== c.status) {
        c.history.push({ from: c.status, to: status, at: new Date() });
        c.status = status;
      }
      if (notes !== undefined) c.notes = notes;
      await c.save();
      res.json(c);
    })
  );

  return router;
};
