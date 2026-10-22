/**
 * api.js - the ONLY file in the client that calls fetch().
 *
 * Every function returns parsed JSON or throws an Error whose `message` comes from the
 * server's { "error": "..." } body, so components can simply show `err.message`.
 * URLs are relative ("/api/..."): Vite's dev proxy / nginx forward them to Express.
 */

/** Core request helper: JSON in, JSON out, uniform error handling. */
async function request(path, { method = 'GET', body, form } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  if (form) init.body = form; // FormData: the browser sets the multipart Content-Type (with boundary) itself
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new Error('Cannot reach the API. Is the server running?');
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

/** Turn { a: 1, b: undefined, c: '' } into "?a=1" (skips empty values). */
function qs(params) {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v); });
  const s = p.toString();
  return s ? `?${s}` : '';
}

export const api = {
  health: () => request('/api/health'),
  listAnalyses: () => request('/api/analyses'),
  getAnalysis: (id) => request(`/api/analyses/${id}`),
  /** Run detection on generated synthetic data. `opts` = { nAccounts, nTransactions, seed, name? } */
  createSynthetic: (opts) => request('/api/analyses', { method: 'POST', body: opts }),
  /** Run detection on an uploaded CSV/JSON File object. */
  uploadFile: (file, name) => {
    const form = new FormData();
    form.append('file', file);
    if (name) form.append('name', name);
    return request('/api/analyses/upload', { method: 'POST', form });
  },
  deleteAnalysis: (id) => request(`/api/analyses/${id}`, { method: 'DELETE' }),
  /** Review queue page. `filters` = { category, pattern, status, q, page, limit } */
  listCases: (id, filters) => request(`/api/analyses/${id}/cases${qs(filters)}`),
  getCase: (id) => request(`/api/cases/${id}`),
  /** Save analyst workflow fields. `patch` = { status? , notes? } */
  updateCase: (id, patch) => request(`/api/cases/${id}`, { method: 'PATCH', body: patch }),
};
