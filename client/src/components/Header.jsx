/**
 * Header.jsx - title bar with live service-health indicators.
 *
 * Polls GET /api/health every 15 s and shows whether MongoDB and the Python detection
 * service are reachable. This is the first place to look when "nothing works".
 */
import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';

export default function Header({ analysis }) {
  const [health, setHealth] = useState(null); // { mongo, detection } | 'unreachable' | null (unknown yet)

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        // /api/health answers 503 (an Error in our api helper) when degraded, so read it via fetch directly
        const res = await fetch('/api/health');
        const data = await res.json();
        if (alive) setHealth(data);
      } catch {
        if (alive) setHealth('unreachable');
      }
    };
    check();
    const id = setInterval(check, 15000);
    return () => { alive = false; clearInterval(id); }; // cleanup when the component unmounts
  }, []);

  const dot = (up) => <span className={`dot ${up ? 'up' : 'down'}`} aria-hidden="true" />;
  const apiUp = health && health !== 'unreachable';

  return (
    <header>
      <h1>UPI fraud pattern detector</h1>
      {analysis && (
        <span>{analysis.meta.days} days from {analysis.meta.start}, {Number(analysis.meta.accountsTotal).toLocaleString('en-IN')} accounts</span>
      )}
      <span title="Health of the backing services">
        {dot(apiUp)}API &nbsp;{dot(apiUp && health.mongo === 'connected')}MongoDB &nbsp;{dot(apiUp && health.detection === 'up')}Detection
      </span>
      <span className="tag">Prototype on synthetic data</span>
    </header>
  );
}
