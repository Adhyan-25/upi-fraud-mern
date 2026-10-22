/**
 * routes/detectors.js
 *
 *   GET /api/detectors -> default detector configuration, feature list and scoring formula,
 *                         proxied from the Python service. The UI shows it in the "How scoring works" box.
 */
const { Router } = require('express');
const asyncHandler = require('../utils/asyncHandler');

module.exports = ({ detection }) => {
  const router = Router();
  router.get('/', asyncHandler(async (_req, res) => res.json(await detection.detectors())));
  return router;
};
