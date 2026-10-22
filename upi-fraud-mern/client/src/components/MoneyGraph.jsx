/**
 * MoneyGraph.jsx - small SVG network diagram of the transfers around one flagged transaction.
 *
 *  - nodes (circles) are accounts, laid out evenly on an ellipse,
 *  - edges (curved arrows) are transfers, labelled with the amount,
 *  - red edges are flagged transfers, the thick edge is the case's own transaction,
 *  - click an account to dim every edge that does not touch it (click again to reset).
 *
 * The layout maths is deliberately simple (no graph library): with <= 10 edges a circle layout reads fine.
 */
import { inr } from '../lib/format.js';

export default function MoneyGraph({ caseData, focusNode, onFocus }) {
  const edges = caseData.graph;
  // unique account names, in order of first appearance
  const names = [...new Set(edges.flatMap((e) => [e.from, e.to]))];
  const n = names.length, W = 380, H = 270, cx = W / 2, cy = H / 2;
  const r = Math.min(100, 38 + n * 10); // radius grows with the number of accounts

  // Place account i at angle (i / n) * 360deg, starting at the top; x is stretched 1.25x to use the wide canvas
  const pos = {};
  names.forEach((a, i) => {
    const t = (i / n) * 2 * Math.PI - Math.PI / 2;
    pos[a] = [cx + r * Math.cos(t) * 1.25, cy + r * Math.sin(t)];
  });

  // Count edges between the same pair so parallel edges get different curvature
  const seen = {};

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Money flow between related accounts">
      {/* arrowhead markers: "a" = grey (normal), "b" = red (flagged) */}
      <defs>
        {['a', 'b'].map((k) => (
          <marker key={k} id={`ar${k}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0L8,4L0,8z" style={{ fill: `var(--${k === 'a' ? 'muted' : 'crit'})` }} />
          </marker>
        ))}
      </defs>

      {edges.map((e, idx) => {
        const key = [e.from, e.to].sort().join('|');
        const k = (seen[key] = (seen[key] || 0) + 1);
        const [x1, y1] = pos[e.from], [x2, y2] = pos[e.to];
        const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
        const off = (e.from < e.to ? 1 : -1) * 16 * k;                         // sideways bend of the curve
        const mx = (x1 + x2) / 2 - (dy / len) * off, my = (y1 + y2) / 2 + (dx / len) * off; // quadratic control point
        const ex = x2 - (dx / len) * 9, ey = y2 - (dy / len) * 9;              // stop the arrow just before the node circle
        const dim = focusNode && e.from !== focusNode && e.to !== focusNode;
        return (
          <g key={idx}>
            <path d={`M${x1},${y1} Q${mx},${my} ${ex},${ey}`} fill="none" markerEnd={`url(#ar${e.flagged ? 'b' : 'a'})`} opacity={dim ? 0.2 : 1}
                  style={{ stroke: `var(--${e.flagged ? 'crit' : 'muted'})`, strokeWidth: e.focus ? 2.6 : 1.2 }}>
              <title>{`${e.from} to ${e.to}: ${inr(e.amount)}${e.flagged ? ' (flagged)' : ''}`}</title>
            </path>
            <text x={(x1 + 2 * mx + x2) / 4} y={(y1 + 2 * my + y2) / 4 - 2} textAnchor="middle" opacity={dim ? 0.2 : 1}>{inr(e.amount)}</text>
          </g>
        );
      })}

      {names.map((a) => {
        const [x, y] = pos[a];
        const isCase = a === caseData.sender || a === caseData.receiver; // highlight the two accounts of this case
        const toggle = () => onFocus(focusNode === a ? null : a);
        return (
          <g key={a} tabIndex={0} role="button" aria-label={`Highlight transfers of ${a}`} style={{ cursor: 'pointer' }}
             onClick={toggle} onKeyDown={(ev) => { if (ev.key === 'Enter') toggle(); }}>
            <circle cx={x} cy={y} r={isCase ? 6 : 4.5} style={{ fill: isCase ? 'var(--accent)' : 'var(--panel)', stroke: 'var(--accent)', strokeWidth: 1.5 }} />
            <text x={x} y={y + (y < cy ? -10 : 17)} textAnchor="middle" style={{ fill: 'var(--text)' }}>{a}</text>
          </g>
        );
      })}
    </svg>
  );
}
