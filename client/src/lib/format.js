/**
 * format.js - small display helpers and label dictionaries shared by several components.
 */

/** Detector id -> label shown to analysts. */
export const PAT = {
  cycle: 'Circular transfer',
  rapid_onward: 'Rapid onward transfer',
  burst: 'Burst of transfers',
  split: 'Split into small transfers',
  unusual_behavior: 'Unusual for this sender',
  irregular_ip: 'Irregular IP address',
};

/** Synthetic scenario name (from the generator's labels) -> label. Note "circular"/"splitting" differ from detector ids. */
export const SCEN = {
  circular: 'Circular transfer',
  rapid_onward: 'Rapid onward transfer',
  burst: 'Burst of transfers',
  splitting: 'Split into small transfers',
  unusual_behavior: 'Unusual for this sender',
  ip_farm: 'Irregular IP address (shared by many accounts)',
};

/** Workflow states, same order as the server enum. */
export const STATUSES = ['Open', 'Investigating', 'Escalated', 'Resolved'];

/** Selectable dataset sizes in the run bar: label -> number of background transactions. */
export const SIZE_PRESETS = [
  { label: '20 thousand (seconds)', n: 20000 },
  { label: '100 thousand', n: 100000 },
  { label: '250 thousand', n: 250000 },
  { label: '1 million (1-2 min)', n: 1000000 },
];

/** Every ML feature the engine can use, grouped (mirrors detection/engine.py FEATURE_GROUPS) - for the "drop features" hint. */
export const FEATURE_GROUPS = {
  core: ['log_amount', 'amount_z', 'sender_cnt_win', 'sender_cnt_1d', 'receiver_in_cnt_1d', 'log_since_prev', 'inflow_ratio', 'pair_cnt_win', 'hour'],
  ip: ['ip_is_new', 'ip_other_tx_win', 'ip_cnt_win', 'ip_distinct_senders'],
  extra: ['device_code', 'channel_collect', 'cross_state', 'log_account_age', 'kyc_full', 'note_len', 'network_code', 'app_major', 'sender_bank_code', 'receiver_bank_code'],
};

/** "a, b ,c" -> ['a','b','c'] (empty entries removed). */
export const parseList = (text) => text.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);

/** 1234567 -> "₹12,34,567" (Indian digit grouping). */
export const inr = (n) => '₹' + Math.round(n).toLocaleString('en-IN');

/** "2026-07-22T14:04:23.000Z" -> "07-22 14:04" (month-day hour:minute, UTC). */
export const when = (iso) => String(iso).slice(5, 16).replace('T', ' ');

/** 0.8358 -> "83.6%"; null/undefined -> "n/a". */
export const pct = (v, digits = 1) => (v == null ? 'n/a' : (v * 100).toFixed(digits) + '%');

/** CSS class for a risk category. */
export const catClass = (cat) => (cat === 'critical' ? 'crit' : 'high');
