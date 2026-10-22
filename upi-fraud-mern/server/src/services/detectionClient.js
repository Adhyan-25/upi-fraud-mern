/**
 * detectionClient.js - the ONLY module that talks to the Python (Flask) detection service.
 *
 * Keeping the HTTP details in one place means the rest of the server just calls
 * `detection.analyze(payload)`. It also makes testing easy: tests inject a fake object with
 * the same three methods instead of needing Python running.
 *
 * Uses the global `fetch` built into Node 18+ (no axios needed).
 */
const HttpError = require('../utils/httpError');

/**
 * @param {{detectionUrl:string, detectionTimeoutMs:number}} config
 * @returns {{health:Function, detectors:Function, analyze:Function}}
 */
function createDetectionClient(config) {
  /** Low-level call with timeout + error translation. */
  async function call(path, { method = 'GET', body, timeoutMs = 10000 } = {}) {
    let res;
    try {
      res = await fetch(`${config.detectionUrl}${path}`, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(timeoutMs), // abort the request if Python takes too long
      });
    } catch (e) {
      // Network error or timeout => 502/504 so the UI can say "the detection service is unavailable"
      const timedOut = e.name === 'TimeoutError' || e.name === 'AbortError';
      throw new HttpError(timedOut ? 504 : 502, timedOut ? 'Detection service timed out' : 'Detection service is unreachable');
    }
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* non-JSON body: handled below */
    }
    if (!res.ok) {
      // 4xx from Python means *our input* was bad (e.g. bad config) => pass the message on as 400/413.
      // 5xx from Python means the service itself failed => 502.
      const status = res.status >= 400 && res.status < 500 ? res.status : 502;
      throw new HttpError(status, (data && data.error) || 'Detection service error');
    }
    return data;
  }

  return {
    /** GET /health of the Flask service */
    health: () => call('/health', { timeoutMs: 3000 }),
    /** GET /detectors - default config, features and scoring description */
    detectors: () => call('/detectors'),
    /** POST /analyze - run the engine. `payload` = { transactions | generate, config?, limit? } */
    analyze: (payload) => call('/analyze', { method: 'POST', body: payload, timeoutMs: config.detectionTimeoutMs }),
  };
}

module.exports = { createDetectionClient };
