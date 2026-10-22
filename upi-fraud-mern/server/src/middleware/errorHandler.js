/**
 * errorHandler.js - the notFound handler and the one central error handler.
 *
 * Whatever goes wrong anywhere in the app ends up here and leaves as a JSON body
 * { "error": "message" } so the React client has ONE shape to deal with.
 * Internal details (stack traces, Mongo messages) are logged but never sent to the client.
 */
const HttpError = require('../utils/httpError');

/** Runs when no route matched. */
function notFound(req, _res, next) {
  next(new HttpError(404, `Route not found: ${req.method} ${req.originalUrl}`));
}

/** Express recognises error handlers by their 4 parameters - do not remove `_next`! */
// eslint-disable-next-line no-unused-vars
function errorHandler(err, _req, res, _next) {
  // Errors we threw on purpose (validation, 404, upstream failures)
  if (err instanceof HttpError) {
    return res.status(err.status).json({ error: err.message, ...(err.details ? { details: err.details } : {}) });
  }
  // multer: file too large / unexpected field
  if (err && err.name === 'MulterError') {
    const status = err.code === 'LIMIT_FILE_SIZE' ? 413 : 400;
    return res.status(status).json({ error: err.code === 'LIMIT_FILE_SIZE' ? 'File too large' : `Upload error: ${err.message}` });
  }
  // body-parser: malformed JSON or payload too large
  if (err && (err.type === 'entity.parse.failed' || err.type === 'entity.too.large')) {
    return res.status(err.status || 400).json({ error: err.type === 'entity.too.large' ? 'Request body too large' : 'Malformed JSON body' });
  }
  // Mongoose validation errors are the client's fault (400)
  if (err && err.name === 'ValidationError') {
    return res.status(400).json({ error: err.message });
  }
  // Anything else is a bug: log it, return a generic message
  console.error('[unhandled error]', err);
  return res.status(500).json({ error: 'Internal server error' });
}

module.exports = { notFound, errorHandler };
