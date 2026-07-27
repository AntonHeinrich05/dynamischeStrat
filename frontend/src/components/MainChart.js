import React, { useEffect, useRef, useState } from 'react';
import { createChart, CandlestickSeries, LineSeries } from 'lightweight-charts';
import './MainChart.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// EMA helper (client-side overlay)
const ema = (values, period) => {
  if (values.length < period) return [];
  const k = 2 / (period + 1);
  const out = [];
  let prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = 0; i < values.length; i++) {
    if (i < period - 1) { out.push(null); continue; }
    if (i === period - 1) { out.push(prev); continue; }
    prev = values[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
};

const MainChart = ({ symbol, candleData }) => {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const ema9Ref = useRef(null);
  const ema50Ref = useRef(null);
  const lastTimeRef = useRef(0);
  const resizeObserverRef = useRef(null);
  const resizeRafRef = useRef(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [bars, setBars] = useState(0);

  // Create chart once
  useEffect(() => {
    if (!chartContainerRef.current) return;
    // European / German time on the chart axis + crosshair tooltip.
    // lightweight-charts renders UTC by default; we override via formatters
    // that use Europe/Berlin so times match German local time incl. DST.
    const berlinTime = (ts, opts) => new Intl.DateTimeFormat('de-DE', {
      timeZone: 'Europe/Berlin', hour12: false, ...opts,
    }).format(new Date(ts * 1000));
    const container = chartContainerRef.current;
    const initialWidth = Math.max(container.clientWidth || 0, 1);
    const initialHeight = Math.max(container.clientHeight || 0, 1);
    const chart = createChart(container, {
      layout: { background: { color: '#121212' }, textColor: '#A1A4B0' },
      grid: { vertLines: { color: '#1E2028' }, horzLines: { color: '#1E2028' } },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: '#2A2D3A',
        tickMarkFormatter: (time) => berlinTime(time, {
          hour: '2-digit', minute: '2-digit',
        }),
      },
      rightPriceScale: { borderColor: '#2A2D3A' },
      crosshair: { mode: 1 },
      localization: {
        locale: 'de-DE',
        timeFormatter: (time) => berlinTime(time, {
          day: '2-digit', month: '2-digit', year: 'numeric',
          hour: '2-digit', minute: '2-digit',
        }),
      },
      // WICHTIG: kein autoSize:true - das erzeugt in bestimmten Flex-Layouts
      // eine Feedback-Schleife (Chart -> Parent -> ResizeObserver -> Chart),
      // wodurch das Chart-Fenster immer kleiner wird. Wir messen selbst.
      autoSize: false,
      width: initialWidth,
      height: initialHeight,
    });
    chartRef.current = chart;
    candleSeriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: '#00FF66', downColor: '#FF3366', borderUpColor: '#00FF66',
      borderDownColor: '#FF3366', wickUpColor: '#00FF66', wickDownColor: '#FF3366',
    });
    ema9Ref.current = chart.addSeries(LineSeries, { color: '#FFD700', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    ema50Ref.current = chart.addSeries(LineSeries, { color: '#00A8FF', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

    // Manuelles Resize per ResizeObserver + rAF-Throttle, mit
    // "letzter angewendeter Größe"-Guard - so triggern wir keine Endlos-Loop.
    let lastAppliedW = initialWidth;
    let lastAppliedH = initialHeight;
    const applySize = () => {
      resizeRafRef.current = null;
      const el = chartContainerRef.current;
      const c = chartRef.current;
      if (!el || !c) return;
      const w = Math.max(Math.floor(el.clientWidth), 1);
      const h = Math.max(Math.floor(el.clientHeight), 1);
      if (w === lastAppliedW && h === lastAppliedH) return; // guard
      lastAppliedW = w;
      lastAppliedH = h;
      try { c.resize(w, h, false); } catch (_) { /* noop */ }
    };
    const scheduleResize = () => {
      if (resizeRafRef.current != null) return;
      resizeRafRef.current = window.requestAnimationFrame(applySize);
    };

    // ResizeObserver auf dem Container (der ist absolute innerhalb .chart-wrap,
    // dadurch beeinflusst seine Größe den Parent nicht -> keine Loop).
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserverRef.current = new ResizeObserver(scheduleResize);
      resizeObserverRef.current.observe(container);
    }
    window.addEventListener('resize', scheduleResize);

    return () => {
      window.removeEventListener('resize', scheduleResize);
      if (resizeRafRef.current != null) {
        window.cancelAnimationFrame(resizeRafRef.current);
        resizeRafRef.current = null;
      }
      if (resizeObserverRef.current) {
        try { resizeObserverRef.current.disconnect(); } catch (_) { /* noop */ }
        resizeObserverRef.current = null;
      }
      try { chart.remove(); } catch (e) { /* noop */ }
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, []);

  // Load historical candles when symbol changes (fixes empty/black chart + Gold crash)
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true); setError(null);
      lastTimeRef.current = 0;
      try {
        const res = await fetch(`${API_URL}/api/klines/${symbol}?limit=200`);
        const data = await res.json();
        const candles = (data.candles || [])
          .map(c => ({ time: Math.floor(c.timestamp / 1000), open: c.open, high: c.high, low: c.low, close: c.close }))
          .filter(c => c.time && Number.isFinite(c.open) && Number.isFinite(c.close));
        // dedupe + sort ascending (lightweight-charts requirement)
        const seen = new Set();
        const clean = [];
        candles.sort((a, b) => a.time - b.time).forEach(c => {
          if (!seen.has(c.time)) { seen.add(c.time); clean.push(c); }
        });
        if (cancelled || !candleSeriesRef.current) return;
        candleSeriesRef.current.setData(clean);
        const closes = clean.map(c => c.close);
        const e9 = ema(closes, 9), e50 = ema(closes, 50);
        ema9Ref.current.setData(clean.map((c, i) => e9[i] != null ? { time: c.time, value: e9[i] } : null).filter(Boolean));
        ema50Ref.current.setData(clean.map((c, i) => e50[i] != null ? { time: c.time, value: e50[i] } : null).filter(Boolean));
        lastTimeRef.current = clean.length ? clean[clean.length - 1].time : 0;
        chartRef.current && chartRef.current.timeScale().fitContent();
        setBars(clean.length);
        setLoading(false);
      } catch (e) {
        if (!cancelled) { setError('Chart konnte nicht geladen werden'); setLoading(false); }
      }
    };
    load();
    return () => { cancelled = true; };
  }, [symbol]);

  // Live forming candle updates (guarded against out-of-order timestamps)
  useEffect(() => {
    if (!candleData || !candleSeriesRef.current) return;
    const time = Math.floor(candleData.timestamp / 1000);
    if (!time || !Number.isFinite(candleData.close)) return;
    if (time < lastTimeRef.current) return; // never update older data -> prevents crash
    try {
      candleSeriesRef.current.update({
        time, open: candleData.open, high: candleData.high,
        low: candleData.low, close: candleData.close,
      });
      lastTimeRef.current = time;
    } catch (e) {
      // swallow chart errors so the whole UI never crashes
      console.warn('chart update skipped', e.message);
    }
  }, [candleData]);

  return (
    <div className="main-chart" data-testid="main-chart">
      <div className="chart-header">
        <div className="chart-title">
          <span className="mono">{symbol}</span>
          <span className="chart-subtitle">1MIN · {bars} bars</span>
        </div>
        <div className="chart-indicators">
          <div className="indicator-label"><div className="indicator-dot" style={{ background: '#FFD700' }}></div><span>EMA 9</span></div>
          <div className="indicator-label"><div className="indicator-dot" style={{ background: '#00A8FF' }}></div><span>EMA 50</span></div>
        </div>
      </div>
      <div className="chart-wrap">
        {loading && <div className="chart-overlay" data-testid="chart-loading">Lade {symbol}...</div>}
        {error && <div className="chart-overlay chart-error" data-testid="chart-error">{error}</div>}
        <div ref={chartContainerRef} className="chart-container" />
      </div>
    </div>
  );
};

export default MainChart;
