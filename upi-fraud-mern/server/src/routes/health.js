/**
 * routes/health.js
 *
 *   GET /api/health   -> { status, mongo, detection }
 *
 * Used by the React header ("API online / detection service online") and by Docker health
 * checks. It never throws: if a dependency is down we report it instead of failing.
 */
const { Router } = require('express');
const db = require('../db');
const asyncHandler = require('../utils/asyncHandler');

module.exports = ({ detection }) => {
  const router = Router();

  router.get(
    '/',
    asyncHandler(async (_req, res) => {
      let detectionState = 'up';
      try {
        await detection.health();
      } catch {
        detectionState = 'down';
      }
      const mongo = db.state();
      const ok = mongo === 'connected' && detectionState === 'up';
      // 200 when everything is healthy, 503 otherwise (load balancers use the status code)
      res.status(ok ? 200 : 503).json({ status: ok ? 'ok' : 'degraded', mongo, detection: detectionState });
    })
  );

  return router;
};
