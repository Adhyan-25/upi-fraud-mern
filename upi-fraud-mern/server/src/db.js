/**
 * db.js - MongoDB connection helper built on Mongoose.
 *
 * Mongoose keeps ONE global connection that all models share, so connect() is called
 * once at start-up (src/index.js) and once per test run (tests/helpers.js).
 */
const mongoose = require('mongoose');

// `strictQuery: true` makes Mongoose ignore filter keys that are not in the schema.
// That is a defence against NoSQL-injection style filters like { "$where": "..." }.
mongoose.set('strictQuery', true);

/**
 * Connect to MongoDB.
 * @param {string} uri connection string, e.g. mongodb://127.0.0.1:27017/upi_fraud
 */
async function connect(uri) {
  // serverSelectionTimeoutMS: fail fast (5 s) with a clear error if MongoDB is not running,
  // instead of Mongoose's default 30 s of silent waiting.
  await mongoose.connect(uri, { serverSelectionTimeoutMS: 5000 });
  return mongoose.connection;
}

/** Close the connection (used on shutdown and at the end of tests). */
async function disconnect() {
  await mongoose.disconnect();
}

/** Human readable connection state for the /api/health endpoint. */
function state() {
  // readyState: 0 disconnected, 1 connected, 2 connecting, 3 disconnecting
  return ['disconnected', 'connected', 'connecting', 'disconnecting'][mongoose.connection.readyState] || 'unknown';
}

module.exports = { connect, disconnect, state };
