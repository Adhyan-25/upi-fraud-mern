/**
 * parseTransactions.js - turn an uploaded file into an array of transaction objects.
 *
 * Accepted formats
 *   CSV  : header row with at least  tx_id,timestamp,sender,receiver,amount,status
 *   JSON : either an array of objects, or { "transactions": [ ... ] }
 *
 * This function only PARSES. Validating individual rows (bad amounts, bad dates, duplicates)
 * is the Python engine's job - it returns rejected rows with reasons instead of failing.
 */
const { parse } = require('csv-parse/sync');
const HttpError = require('./httpError');

/** Columns the engine needs. We check the header early to give a friendly error. */
const REQUIRED = ['tx_id', 'timestamp', 'sender', 'receiver', 'amount', 'status'];

/**
 * @param {Buffer} buffer raw file content
 * @param {string} filename original name (extension decides CSV vs JSON)
 * @returns {object[]} rows
 */
function parseTransactions(buffer, filename = '') {
  const text = buffer.toString('utf8').replace(/^﻿/, ''); // strip a UTF-8 byte-order mark if Excel added one
  const isJson = /\.json$/i.test(filename) || /^\s*[[{]/.test(text);
  let rows;

  if (isJson) {
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new HttpError(400, 'File is not valid JSON');
    }
    rows = Array.isArray(data) ? data : data && data.transactions;
    if (!Array.isArray(rows)) {
      throw new HttpError(400, 'JSON must be an array of transactions or an object with a "transactions" array');
    }
    if (!rows.every((r) => r && typeof r === 'object' && !Array.isArray(r))) {
      throw new HttpError(400, 'Every transaction must be a JSON object');
    }
  } else {
    try {
      rows = parse(text, {
        columns: (header) => header.map((h) => h.trim()), // first line = column names
        skip_empty_lines: true,
        trim: true,
        relax_column_count: true, // short/long rows are handled by the engine's validation instead of crashing here
        cast: false,              // keep everything as strings; the engine converts and validates numbers/dates
        // Empty cells become null (not ""), so the engine's validation reports them as missing values
        // (an empty-string tx_id or sender would otherwise slip through as a "present" value).
        on_record: (rec) => {
          for (const k of Object.keys(rec)) if (rec[k] === '') rec[k] = null;
          return rec;
        },
      });
    } catch (e) {
      throw new HttpError(400, `Could not parse CSV: ${e.message}`);
    }
  }

  if (rows.length) {
    const present = new Set(Object.keys(rows[0]));
    const missing = REQUIRED.filter((c) => !present.has(c));
    if (missing.length) throw new HttpError(400, `Missing required columns: ${missing.join(', ')}`);
  }
  return rows;
}

module.exports = { parseTransactions, REQUIRED };
