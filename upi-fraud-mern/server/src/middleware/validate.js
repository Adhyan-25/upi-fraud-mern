/**
 * validate.js - request validation helpers built on zod.
 *
 * Each helper parses one part of the request (body / query / params), REPLACES it with the
 * cleaned, type-coerced result, and forwards a 400 error with readable messages when the
 * data does not match the schema. Handlers can then trust `req.body` / `req.query`.
 */
const mongoose = require('mongoose');
const HttpError = require('../utils/httpError');

/** Build a middleware that validates `req[part]` against a zod schema. */
function validate(part, schema) {
  return (req, _res, next) => {
    const result = schema.safeParse(req[part]);
    if (!result.success) {
      // Turn zod issues into [{ path: "limit", message: "..." }]
      const issues = result.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message }));
      return next(new HttpError(400, `Invalid ${part}: ${issues.map((i) => (i.path ? i.path + ' ' : '') + i.message).join('; ')}`, issues));
    }
    // Express 4 allows reassigning req.query/req.body; keep the parsed (coerced) values
    req[part] = result.data;
    next();
  };
}

/**
 * Reject malformed MongoDB ObjectIds with a clean 400 instead of letting Mongoose throw a CastError.
 * @param {string} name route param name, e.g. 'id'
 */
function objectIdParam(name = 'id') {
  return (req, _res, next) => {
    if (!mongoose.isValidObjectId(req.params[name]) || String(req.params[name]).length !== 24) {
      return next(new HttpError(400, `Invalid ${name}`));
    }
    next();
  };
}

module.exports = { validate, objectIdParam };
