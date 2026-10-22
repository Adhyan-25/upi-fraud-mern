/**
 * config.js - the ONE place where environment variables are read.
 *
 * Every other module imports this object instead of touching process.env, which means:
 *   - defaults live in a single file,
 *   - numbers are parsed once (env vars are always strings),
 *   - tests can build their own config object without mutating the environment.
 */
require('dotenv').config(); // loads server/.env into process.env (silently does nothing if the file is missing)

/** Parse an env var as a positive integer, falling back to `def` if it is missing or invalid. */
function int(name, def) {
  const n = Number.parseInt(process.env[name], 10);
  return Number.isFinite(n) && n > 0 ? n : def;
}

const config = {
  env: process.env.NODE_ENV || 'development',
  port: int('PORT', 5000),
  mongoUri: process.env.MONGODB_URI || 'mongodb://127.0.0.1:27017/upi_fraud',
  detectionUrl: (process.env.DETECTION_URL || 'http://127.0.0.1:5001').replace(/\/+$/, ''), // strip trailing slashes
  // Default 15 minutes: analysing ONE MILLION transactions (+ 5-fold cross-validation) takes a minute or two on a laptop,
  // and a slow machine can need several times that.
  detectionTimeoutMs: int('DETECTION_TIMEOUT_MS', 900000),
  // CORS_ORIGIN="http://a.com,http://b.com" => ['http://a.com','http://b.com']
  corsOrigins: (process.env.CORS_ORIGIN || 'http://localhost:5173').split(',').map((s) => s.trim()).filter(Boolean),
  maxUploadBytes: int('MAX_UPLOAD_MB', 50) * 1024 * 1024,
};

module.exports = config;
