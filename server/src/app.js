/**
 * app.js - builds the Express application (but does NOT start listening).
 *
 * Separating "build the app" (here) from "start the server" (index.js) lets the tests
 * import createApp() and call it with supertest on an ephemeral port.
 *
 * `deps` are injected so tests can pass a fake detection client:
 *   createApp({ config, detection })
 */
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const { notFound, errorHandler } = require('./middleware/errorHandler');

function createApp(deps) {
  const { config } = deps;
  const app = express();

  // If the API runs behind nginx / a load balancer, trust its X-Forwarded-For header
  // so the rate limiter sees real client IPs (1 = trust exactly one proxy hop).
  app.set('trust proxy', 1);

  // ---- global middleware (order matters: they run top to bottom) ----
  app.use(helmet());                                  // sets security headers (X-Content-Type-Options etc.)
  app.use(cors({ origin: config.corsOrigins }));      // only the listed browser origins may call the API
  if (config.env !== 'test') app.use(morgan('dev'));  // one log line per request
  app.use(express.json({ limit: '1mb' }));            // parse JSON bodies (uploads use multer instead)

  // Basic abuse protection: 300 requests / minute / IP overall...
  app.use('/api', rateLimit({ windowMs: 60_000, limit: 300, standardHeaders: true, legacyHeaders: false }));
  // ...and a stricter limit on the expensive endpoints that run the ML engine.
  const heavy = rateLimit({ windowMs: 60_000, limit: 20, standardHeaders: true, legacyHeaders: false, message: { error: 'Too many analysis requests, slow down' } });
  app.post('/api/analyses', heavy);
  app.post('/api/analyses/upload', heavy);

  // ---- routes ----
  app.use('/api/health', require('./routes/health')(deps));
  app.use('/api/detectors', require('./routes/detectors')(deps));
  app.use('/api/analyses', require('./routes/analyses')(deps));
  app.use('/api/cases', require('./routes/cases')(deps));

  // ---- fallbacks (must come LAST) ----
  app.use(notFound);
  app.use(errorHandler);
  return app;
}

module.exports = { createApp };
