/**
 * tests/api.test.js - integration tests for the Express API using Node's built-in test runner.
 *
 * Run with:  npm test
 *
 * The detection service is replaced by a fake (see helpers.js); MongoDB is real (in-memory
 * or whatever TEST_MONGODB_URI points at). If no MongoDB can be started the DB-backed tests
 * are skipped with a clear message.
 */
const { describe, it, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const request = require('supertest');
const { createApp } = require('../src/app');
const { Analysis } = require('../src/models/Analysis');
const { Case } = require('../src/models/Case');
const { HttpError } = { HttpError: require('../src/utils/httpError') };
const { startDb, fakeDetection } = require('./helpers');

const config = { env: 'test', corsOrigins: ['http://localhost:5173'], maxUploadBytes: 1024 * 1024, detectionUrl: 'x', detectionTimeoutMs: 1000 };

describe('API', () => {
  let stop, detection, app;

  before(async () => { stop = await startDb(); });
  after(async () => { if (stop) await stop(); });
  beforeEach(async () => {
    detection = fakeDetection();
    app = createApp({ config, detection });
    if (stop) await Promise.all([Analysis.deleteMany({}), Case.deleteMany({})]); // fresh state per test
  });

  // ---- tests that need no database ----
  it('GET /api/health reports degraded when MongoDB is down or detection is down', async () => {
    const res = await request(createApp({ config, detection: { ...detection, health: async () => { throw new Error('x'); } } })).get('/api/health');
    assert.equal(res.status, 503);
    assert.equal(res.body.detection, 'down');
  });

  it('unknown routes return a JSON 404', async () => {
    const res = await request(app).get('/api/nope');
    assert.equal(res.status, 404);
    assert.match(res.body.error, /Route not found/);
  });

  it('GET /api/detectors proxies the detection service', async () => {
    const res = await request(app).get('/api/detectors');
    assert.equal(res.status, 200);
    assert.equal(res.body.config.threshold, 50);
  });

  it('rejects invalid generate options with 400 before calling Python', async () => {
    const res = await request(app).post('/api/analyses').send({ nAccounts: 5 });
    assert.equal(res.status, 400);
    assert.equal(detection.calls.length, 0);
  });

  it('accepts up to one million transactions and rejects more', async () => {
    assert.equal((await request(app).post('/api/analyses').send({ nTransactions: 1000001 })).status, 400);
    assert.equal((await request(app).post('/api/analyses').send({ cvFolds: 11 })).status, 400);
    assert.equal((await request(app).post('/api/analyses').send({ cvMode: 'random' })).status, 400);
    assert.equal((await request(app).post('/api/analyses').send({ nAccounts: 59 })).status, 400);
    assert.equal(detection.calls.length, 0);
  });

  it('rejects malformed JSON with 400', async () => {
    const res = await request(app).post('/api/analyses').set('Content-Type', 'application/json').send('{bad');
    assert.equal(res.status, 400);
  });

  it('rejects a malformed ObjectId with 400', async () => {
    assert.equal((await request(app).get('/api/cases/not-an-id')).status, 400);
  });

  it('upload without a file is a 400', async () => {
    assert.equal((await request(app).post('/api/analyses/upload')).status, 400);
  });

  it('upload with missing CSV columns is a 400 with the column names', async () => {
    const res = await request(app).post('/api/analyses/upload').attach('file', Buffer.from('a,b\n1,2\n'), 'x.csv');
    assert.equal(res.status, 400);
    assert.match(res.body.error, /tx_id/);
  });

  // ---- tests that need MongoDB ----
  const db = (name, fn) => it(name, async (t) => { if (!stop) return t.skip('no MongoDB available'); await fn(t); });

  db('POST /api/analyses stores the run and its cases', async () => {
    const res = await request(app).post('/api/analyses').send({ nAccounts: 100, nTransactions: 1000, seed: 7, limit: 5 });
    assert.equal(res.status, 201);
    assert.equal(res.body.source, 'synthetic');
    assert.equal(res.body.caseCount, 5);
    assert.equal(res.body.patterns.cycle, 3);                         // Map serialised as plain object
    assert.deepEqual(detection.calls[0].generate, { n_accounts: 100, n_transactions: 1000, seed: 7 }); // camelCase -> snake_case
    assert.equal(await Case.countDocuments({ analysis: res.body._id }), 5);
  });

  db('one million transactions: accounts are left to the engine, 5-fold CV is requested by default', async () => {
    const res = await request(app).post('/api/analyses').send({ nTransactions: 1000000 });
    assert.equal(res.status, 201);
    assert.equal(res.body.name, 'Synthetic 10,00,000 transactions (seed 42)');        // Indian digit grouping
    const sent = detection.calls[0];
    assert.equal(sent.generate.n_transactions, 1000000);
    assert.equal('n_accounts' in JSON.parse(JSON.stringify(sent.generate)), false);   // omitted => engine picks ~n/10
    assert.deepEqual(sent.cross_validation, { enabled: true, folds: 5, mode: 'stratified' });
  });

  db('cross-validation result is stored on the analysis; it can be switched off or reconfigured', async () => {
    const on = await request(app).post('/api/analyses').send({ cvFolds: 3, cvMode: 'time' });
    assert.equal(on.body.crossValidation.folds, 3);
    assert.equal(on.body.crossValidation.mode, 'time');
    const stored = await request(app).get(`/api/analyses/${on.body._id}`);
    assert.equal(stored.body.crossValidation.mean.f1, 0.7);
    const off = await request(app).post('/api/analyses').send({ cv: false });
    assert.equal(off.body.crossValidation, null);
    assert.equal(detection.calls[1].cross_validation.enabled, false);
  });

  db('disabledFeatures is merged into the detector config', async () => {
    await request(app).post('/api/analyses').send({ disabledFeatures: ['note_len', 'app_major'], config: { threshold: 60 } });
    assert.deepEqual(detection.calls[0].config, { threshold: 60, disabled_features: ['note_len', 'app_major'] });
  });

  db('cases keep the ip / device / channel context and the irregular_ip pattern is filterable', async () => {
    const { body } = await request(app).post('/api/analyses').send({});
    const first = (await request(app).get(`/api/analyses/${body._id}/cases`)).body.items[0];
    const full = await request(app).get(`/api/cases/${first._id}`);
    assert.equal(full.body.ip, '10.0.0.0');
    assert.equal(full.body.device, 'android');
    assert.equal((await request(app).get(`/api/analyses/${body._id}/cases?pattern=irregular_ip`)).status, 200);
  });

  db('POST /api/analyses/upload parses CSV and forwards transactions', async () => {
    const csv = 'tx_id,timestamp,sender,receiver,amount,status\nT1,2026-07-01T00:00:00Z,A,B,100,SUCCESS\nT2,2026-07-01T00:01:00Z,B,C,50,SUCCESS\n';
    const res = await request(app).post('/api/analyses/upload').field('name', 'my file').attach('file', Buffer.from(csv), 'tx.csv');
    assert.equal(res.status, 201);
    assert.equal(res.body.name, 'my file');
    assert.equal(res.body.source, 'upload');
    assert.equal(res.body.evaluation, null);                           // no labels => no evaluation
    assert.equal(detection.calls[0].transactions.length, 2);
    assert.equal(detection.calls[0].transactions[0].sender, 'A');
  });

  db('GET /api/analyses lists newest first and omits heavy fields', async () => {
    await request(app).post('/api/analyses').send({ name: 'first' });
    await request(app).post('/api/analyses').send({ name: 'second' });
    const res = await request(app).get('/api/analyses');
    assert.equal(res.body.length, 2);
    assert.equal(res.body[0].name, 'second');
    assert.equal(res.body[0].volume, undefined);
  });

  db('GET /api/analyses/:id includes status counts; 404 for unknown id', async () => {
    const { body } = await request(app).post('/api/analyses').send({});
    const res = await request(app).get(`/api/analyses/${body._id}`);
    assert.equal(res.status, 200);
    assert.deepEqual(res.body.statusCounts, { Open: 5, Investigating: 0, Escalated: 0, Resolved: 0 });
    assert.equal((await request(app).get('/api/analyses/64b7f0f0f0f0f0f0f0f0f0f0')).status, 404);
  });

  db('GET /api/analyses/:id/cases paginates, filters and searches', async () => {
    const { body } = await request(app).post('/api/analyses').send({});
    const base = `/api/analyses/${body._id}/cases`;
    const all = await request(app).get(base);
    assert.equal(all.body.total, 5);
    assert.deepEqual(all.body.items.map((c) => c.riskScore), [100, 90, 80, 70, 60]); // highest risk first
    const p2 = await request(app).get(`${base}?limit=2&page=2`);
    assert.equal(p2.body.items.length, 2);
    assert.equal(p2.body.pages, 3);
    assert.equal((await request(app).get(`${base}?category=critical`)).body.total, 3);
    assert.equal((await request(app).get(`${base}?pattern=burst`)).body.total, 2);
    assert.equal((await request(app).get(`${base}?q=acc00003`)).body.total, 1);        // case-insensitive
    assert.equal((await request(app).get(`${base}?q=${encodeURIComponent('.*')}`)).body.total, 0); // regex is escaped
    assert.equal((await request(app).get(`${base}?limit=9999`)).status, 400);
  });

  db('PATCH /api/cases/:id updates status + notes and records history', async () => {
    const { body } = await request(app).post('/api/analyses').send({});
    const first = (await request(app).get(`/api/analyses/${body._id}/cases`)).body.items[0];
    const res = await request(app).patch(`/api/cases/${first._id}`).send({ status: 'Investigating', notes: 'called the bank' });
    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'Investigating');
    assert.equal(res.body.notes, 'called the bank');
    assert.equal(res.body.history.length, 1);
    assert.equal(res.body.history[0].from, 'Open');
    // same status again => no new history entry
    const again = await request(app).patch(`/api/cases/${first._id}`).send({ status: 'Investigating' });
    assert.equal(again.body.history.length, 1);
    // detection output is immutable and unknown values are refused
    assert.equal((await request(app).patch(`/api/cases/${first._id}`).send({ riskScore: 0 })).status, 400);
    assert.equal((await request(app).patch(`/api/cases/${first._id}`).send({ status: 'Banana' })).status, 400);
    assert.equal((await request(app).patch(`/api/cases/${first._id}`).send({})).status, 400);
  });

  db('GET /api/cases/:id returns the graph and evidence', async () => {
    const { body } = await request(app).post('/api/analyses').send({});
    const first = (await request(app).get(`/api/analyses/${body._id}/cases`)).body.items[0];
    const res = await request(app).get(`/api/cases/${first._id}`);
    assert.equal(res.body.graph.length, 1);
    assert.equal(res.body.evidence[0].text, 'canned explanation');
  });

  db('DELETE /api/analyses/:id cascades to cases', async () => {
    const { body } = await request(app).post('/api/analyses').send({});
    const res = await request(app).delete(`/api/analyses/${body._id}`);
    assert.deepEqual(res.body, { deleted: true, casesDeleted: 5 });
    assert.equal(await Case.countDocuments({}), 0);
    assert.equal((await request(app).delete(`/api/analyses/${body._id}`)).status, 404);
  });

  db('detection service failures surface as 502/504/400 and store nothing', async () => {
    for (const [err, status] of [[new HttpError(502, 'down'), 502], [new HttpError(504, 'slow'), 504], [new HttpError(400, 'bad config'), 400]]) {
      const a = createApp({ config, detection: fakeDetection({ fail: err }) });
      const res = await request(a).post('/api/analyses').send({});
      assert.equal(res.status, status);
      assert.equal(res.body.error, err.message);
    }
    assert.equal(await Analysis.countDocuments({}), 0);
  });
});
