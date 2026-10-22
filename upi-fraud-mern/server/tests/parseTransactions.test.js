/**
 * tests/parseTransactions.test.js - unit tests for the CSV/JSON upload parser (no DB, no HTTP).
 */
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { parseTransactions } = require('../src/utils/parseTransactions');

const HEADER = 'tx_id,timestamp,sender,receiver,amount,status';

describe('parseTransactions', () => {
  it('parses CSV, trims whitespace and keeps values as strings', () => {
    const rows = parseTransactions(Buffer.from(`${HEADER}\n T1 , 2026-07-01T00:00:00Z , A , B , 12.5 , SUCCESS \n`), 'x.csv');
    assert.deepEqual(rows, [{ tx_id: 'T1', timestamp: '2026-07-01T00:00:00Z', sender: 'A', receiver: 'B', amount: '12.5', status: 'SUCCESS' }]);
  });

  it('handles a UTF-8 BOM (Excel exports) and blank lines', () => {
    const rows = parseTransactions(Buffer.from(`﻿${HEADER}\n\nT1,t,A,B,1,SUCCESS\n`), 'x.csv');
    assert.equal(rows.length, 1);
    assert.equal(rows[0].tx_id, 'T1');
  });

  it('turns empty CSV cells into null so the engine can reject them', () => {
    const rows = parseTransactions(Buffer.from(`${HEADER}\n,2026-07-01T00:00:00Z,A,,10,SUCCESS\n`), 'x.csv');
    assert.equal(rows[0].tx_id, null);
    assert.equal(rows[0].receiver, null);
  });

  it('accepts JSON arrays and {transactions: []}', () => {
    const t = { tx_id: 'T1', timestamp: 't', sender: 'A', receiver: 'B', amount: 1, status: 'SUCCESS' };
    assert.equal(parseTransactions(Buffer.from(JSON.stringify([t])), 'x.json').length, 1);
    assert.equal(parseTransactions(Buffer.from(JSON.stringify({ transactions: [t] })), 'x.json').length, 1);
  });

  it('rejects invalid JSON, wrong shapes and missing columns', () => {
    assert.throws(() => parseTransactions(Buffer.from('{oops'), 'x.json'), /not valid JSON/);
    assert.throws(() => parseTransactions(Buffer.from('{"a":1}'), 'x.json'), /array of transactions/);
    assert.throws(() => parseTransactions(Buffer.from('[1,2]'), 'x.json'), /JSON object/);
    assert.throws(() => parseTransactions(Buffer.from('a,b\n1,2\n'), 'x.csv'), /Missing required columns/);
  });

  it('returns an empty array for a header-only CSV (the engine reports "no valid transactions")', () => {
    assert.deepEqual(parseTransactions(Buffer.from(`${HEADER}\n`), 'x.csv'), []);
  });
});
