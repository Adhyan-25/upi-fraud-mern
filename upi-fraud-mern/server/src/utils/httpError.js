/**
 * httpError.js - an Error that carries an HTTP status code.
 *
 * Route handlers `throw new HttpError(404, 'Case not found')`; the central error handler
 * (middleware/errorHandler.js) turns it into a JSON response with that status.
 */
class HttpError extends Error {
  /**
   * @param {number} status HTTP status code (400, 404, 502 ...)
   * @param {string} message safe, user-facing message
   * @param {object} [details] optional extra info (e.g. validation issues)
   */
  constructor(status, message, details) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
    this.details = details;
  }
}

module.exports = HttpError;
