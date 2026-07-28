import React, { useMemo, useState } from 'react';
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceArea, ReferenceLine,
} from 'recharts';

export const REGIME_COLORS = ['#30D158', '#FF453A', '#FFD60A', '#64D2FF', '#BF5AF2',
  '#FF9F0A', '#5E5CE6', '#66D4CF', '#FF6482', '#AC8E68'];

const fmtDate = (ts) => {
  const d = new Date(ts);
  return `${d.getDate()}.${d.getMonth() + 1}.${String(d.getFullYear()).slice(2)}`;
};

/**
 * Kursverlauf mit farbig hinterlegten Regime-Abschnitten.
 * prices: [[ts, close], ...] · segments: [{regime, from_ts, to_ts}]
 * regimes: [{id,label}] · trainEndTs: optionale Trennlinie Training/Holdout
 */
export default function RegimeChart({ title, prices, segments, regimes, trainEndTs, height = 190 }) {
  const [hidden, setHidden] = useState({});
  const data = useMemo(() => (prices || []).map(p => ({ t: p[0], c: p[1] })), [prices]);
  if (!data.length) return null;
  const [min, max] = data.reduce((a, p) => [Math.min(a[0], p.c), Math.max(a[1], p.c)],
    [Infinity, -Infinity]);
  const pad = (max - min) * 0.04;
  const labelOf = (rid) => (regimes || []).find(r => r.id === rid)?.label || `Regime ${rid + 1}`;

  return (
    <div className="rl-chart" data-testid={`regime-chart-${title || 'chart'}`}>
      {title && <div className="rl-chart-title">{title}</div>}
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          {(segments || []).filter(s => !hidden[s.regime]).map((s, i) => (
            <ReferenceArea key={i} x1={s.from_ts} x2={s.to_ts}
              y1={min - pad} y2={max + pad}
              fill={REGIME_COLORS[s.regime % REGIME_COLORS.length]}
              fillOpacity={0.16} strokeOpacity={0} />
          ))}
          {trainEndTs && (
            <ReferenceLine x={trainEndTs} stroke="#ffa502" strokeDasharray="4 4"
              label={{ value: 'Holdout →', fill: '#ffa502', fontSize: 10, position: 'insideTopRight' }} />
          )}
          <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']}
            tickFormatter={fmtDate} tick={{ fontSize: 10, fill: '#8b90a0' }}
            stroke="#262a38" />
          <YAxis domain={[min - pad, max + pad]} tick={{ fontSize: 10, fill: '#8b90a0' }}
            stroke="#262a38" width={62}
            tickFormatter={(v) => (v >= 1000 ? v.toFixed(0) : v.toPrecision(4))} />
          <Tooltip
            contentStyle={{ background: '#12141d', border: '1px solid #262a38', fontSize: 11 }}
            labelFormatter={(ts) => {
              const seg = (segments || []).find(s => ts >= s.from_ts && ts <= s.to_ts);
              return `${new Date(ts).toLocaleString('de-DE')}${seg ? ` · ${labelOf(seg.regime)}` : ''}`;
            }}
            formatter={(v) => [Number(v).toPrecision(6), 'Kurs']} />
          <Line dataKey="c" dot={false} stroke="#c9cddb" strokeWidth={1.4} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="rl-legend">
        {(regimes || []).map(r => (
          <button key={r.id}
            className={`rl-legend-item ${hidden[r.id] ? 'off' : ''}`}
            onClick={() => setHidden(h => ({ ...h, [r.id]: !h[r.id] }))}
            data-testid={`regime-legend-${r.id}`}
            title="Klicken zum Ein-/Ausblenden der Markierung">
            <span className="rl-dot" style={{ background: REGIME_COLORS[r.id % REGIME_COLORS.length] }} />
            #{r.id + 1} {r.label}
          </button>
        ))}
      </div>
    </div>
  );
}
