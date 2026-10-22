/**
 * asyncHandler.js - wraps an async route handler so rejected promises reach Express' error handler.
 *
 * Express 4 does NOT catch errors thrown inside `async` handlers. Without this wrapper an
 * exception would become an unhandled promise rejection and the request would hang forever.
 *
 *   router.get('/x', asyncHandler(async (req, res) => { ... throw new HttpError(404, '...') }));
 */
module.exports = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
