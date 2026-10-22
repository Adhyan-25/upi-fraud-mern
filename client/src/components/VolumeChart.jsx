/**
 * VolumeChart.jsx - hand-drawn SVG timeline (no chart library needed).
 *
 * Top band  : area chart of transactions per day.
 * Bottom band: red bars of flagged transactions per day (hover a bar for a tooltip).
 *
 * The SVG uses a fixed viewBox (640x146) and scales to the container width.
 */
export default function VolumeChart({ volume, start }) {
  const t = volume.total, f = volume.flagged, n = t.length;
  if (n < 2) return <p className="note">Not enough days to draw a chart.</p>;

  const W = 640, L = 30, R = 6;                                // width, left & right padding
  const x = (i) => L + (i * (W - L - R)) / (n - 1);            // day index -> x pixel
  const mt = Math.max(...t), mf = Math.max(...f, 1);          // peaks (mf >= 1 avoids divide-by-zero)
  // Convert a day index to "MM-DD" using the run's first day
  const day = (i) => new Date(Date.parse(start) + i * 864e5).toISOString().slice(5, 10);
  const bw = Math.max(2, (W - L - R) / n - 1.5);               // bar width

  // SVG path for the area: start at bottom-left, trace the volume line, close at bottom-right
  const area = `M${x(0)},64 ` + t.map((v, i) => `L${x(i).toFixed(1)},${(64 - (v / mt) * 54).toFixed(1)}`).join(' ') + ` L${x(n - 1)},64 Z`;

  return (
    <svg viewBox="0 0 640 146" width="100%" role="img" aria-label="Daily transaction volume and flagged counts">
      <text x="0" y="10">Transactions per day, peak {mt}</text>
      <path d={area} style={{ fill: 'var(--vol)' }} />
      <text x="0" y="86">Flagged per day, peak {mf}</text>
      {f.map((v, i) => v > 0 && (
        <rect key={i} x={x(i) - bw / 2} y={130 - (v / mf) * 38} width={bw} height={(v / mf) * 38} style={{ fill: 'var(--crit)' }}>
          <title>{`${day(i)}: ${v} flagged of ${t[i]}`}</title>
        </rect>
      ))}
      <line x1={L} x2={W - R} y1="130" y2="130" style={{ stroke: 'var(--line)' }} />
      {/* x-axis labels: first, middle and last day */}
      {[0, Math.floor(n / 2), n - 1].map((i) => (
        <text key={i} x={x(i)} y="142" textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>{day(i)}</text>
      ))}
    </svg>
  );
}
