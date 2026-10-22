/**
 * tests/helpers.js - shared test plumbing.
 *
 * Provides:
 *   - startDb(): connects Mongoose to a throw-away database. Uses TEST_MONGODB_URI if set
 *     (e.g. a Mongo running in Docker), otherwise downloads/starts mongodb-memory-server.
 *     Returns null when neither is possible so the suite can SKIP instead of failing.
 *   - fakeDetection(): a stand-in for the Python service with canned, realistic output, so
 *     the API tests are fast and do not need Python running.
 */
const mongoose = require('mongoose');

/** Connect to a test database. Returns a cleanup function, or null if no MongoDB is available. */
async function startDb() {
  let memory = null;
  let uri = process.env.TEST_MONGODB_URI;
  if (!uri) {
    try {
      const { MongoMemoryServer } = require('mongodb-memory-server');
      memory = await MongoMemoryServer.create();
      uri = memory.getUri('upi_fraud_test');
    } catch (e) {
      console.warn(`[tests] no MongoDB available (${e.message.split('\n')[0]}) - skipping DB tests`);
      return null;
    }
  }
  await mongoose.connect(uri, { serverSelectionTimeoutMS: 5000 });
  if (process.env.TEST_MONGODB_URI) await mongoose.connection.dropDatabase(); // start clean on a shared server
  return async () => {
    await mongoose.disconnect();
    if (memory) await memory.stop();
  };
}

/** One canned case in the shape produced by detection/report.py build_report(). */
function cannedCase(i, overrides = {}) {
  return {
    txId: `TX000${i}`, timestamp: `2026-07-0${(i % 8) + 1}T10:00:00Z`, sender: `ACC0000${i}`, receiver: `ACC0010${i}`,
    amount: 10000 + i, riskScore: 100 - i * 10, riskCategory: i < 3 ? 'critical' : 'high', mlPercentile: 0.9,
    ip: `10.0.0.${i}`, device: 'android', channel: 'pay',
    evidence: [{ pattern: i % 2 ? 'burst' : 'cycle', weight: 40, text: 'canned explanation' }],
    graph: [{ from: `ACC0000${i}`, to: `ACC0010${i}`, amount: 10000 + i, flagged: true, focus: true }],
    ...overrides,
  };
}

/**
 * A fake detection client. `calls` records every payload it received so tests can assert
 * what Express forwarded to Python. Pass `fail` to simulate Python errors.
 */
function fakeDetection({ fail } = {}) {
  const calls = [];
  return {
    calls,
    health: async () => ({ status: 'ok' }),
    detectors: async () => ({ config: { threshold: 50 }, features: ['log_amount'] }),
    analyze: async (payload) => {
      calls.push(payload);
      if (fail) throw fail;
      const cases = Array.from({ length: 5 }, (_, i) => cannedCase(i));
      return {
        config: { threshold: 50 },
        quality: { rows_in: 100, rejected: 1, duplicates_removed: 2, failed_excluded: 3, rows_analyzed: 94 },
        rejected_rows: [{ tx_id: 'bad', reject_reason: 'invalid_amount' }],
        warnings: [], timings: { total_s: 0.1 },
        dashboard: {
          meta: { records: 94, flagged: 5, critical: 3, high: 2, accounts: 10, accounts_total: 20, days: 3, start: '2026-07-01',
                  duplicates: 2, rejected: 1, threshold: 50, runtime: 0.1 },
          volume: { total: [30, 32, 32], flagged: [1, 2, 2] },
          patterns: { burst: 2, cycle: 3 },
          evaluation: payload.generate ? { precision: 0.8, recall: 0.6, confusion: { tp: 1, fp: 0, tn: 5, fn: 1 } } : null,
          // like the real service: cross-validation only when requested AND the data is synthetic
          crossValidation: payload.generate && payload.cross_validation && payload.cross_validation.enabled
            ? { folds: payload.cross_validation.folds, mode: payload.cross_validation.mode, perFold: [{ fold: 1, f1: 0.7 }], mean: { f1: 0.7 }, std: { f1: 0.01 } }
            : null,
          cases,
        },
      };
    },
  };
}

module.exports = { startDb, fakeDetection, cannedCase };
