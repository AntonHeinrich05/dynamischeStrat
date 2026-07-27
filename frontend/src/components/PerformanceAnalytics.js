import React, { useState, useEffect, useCallback } from 'react';
import { TrendUp, TrendDown, Target, Clock, ChartBar, Lightning, CheckCircle, XCircle, Trash, Warning, Sparkle, CaretDown } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders } from '../auth';
import './PerformanceAnalytics.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmtTime = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('de-DE', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      second: '2-digit', timeZone: 'Europe/Berlin',
    });
  } catch { return '—'; }
};

const fmtDur = (s) => {
  if (s == null) return '—';
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
};

const fmtPct = (p) => (p == null ? '' : `${p > 0 ? '+' : ''}${p}%`);

// One price level row in the ladder (Entry / SL / TP1 / Full-TP / Exit)
const LevelRow = ({ label, value, pct, cls, hit }) => {
  if (value == null || value === 0) return null;
  return (
    <div className={`lvl-row ${cls || ''}`}>
      <span className="lvl-label">{label}{hit ? ' ✓' : ''}</span>
      <span className="lvl-value mono">{value}</span>
      {pct != null && <span className="lvl-pct mono">{fmtPct(pct)}</span>}
    </div>
  );
};

const TradeDetailCard = ({ t, stratName, getCoinName }) => {
  const [open, setOpen] = useState(false);
  const c = t.computed || {};
  const isLive = t.mode === 'live';
  const closed = t.status === 'closed';
  const resultMeta = t.result === 'win'
  ? { label: 'G', cls: 'res-win' }
  : t.result === 'loss'
    ? { label: 'V', cls: 'res-loss' }
    : t.result === 'breakeven'
      ? { label: 'BEP', cls: 'res-be' }
      : { label: 'OFFEN', cls: 'res-open' };
  const pnl = (!closed && c.live_pnl != null) ? c.live_pnl : (t.realized_pnl || 0);
  const pnlPct = c.pnl_pct;

  return (
    <div className={`tdc ${open ? 'tdc-open' : ''}`} data-testid={`trade-card-${t.id}`}>
      <button className="tdc-head" onClick={() => setOpen(o => !o)} data-testid={`trade-card-toggle-${t.id}`}>
  <span className="tdc-main">
    <span className={`mode-tag ${isLive ? 'mode-live' : 'mode-paper'}`} data-testid={`trade-mode-${t.id}`}>
      {isLive ? 'LIVE' : 'PAPER'}
    </span>
    <span className={`badge ${t.side === 'LONG' ? 'badge-long' : 'badge-short'}`}>{t.side}</span>
    <span className="mono text-secondary tdc-coin">{getCoinName(t.symbol)}</span>
    <span className={`tdc-result ${resultMeta.cls}`}>{resultMeta.label}</span>
    <span className={`mono tdc-pnl ${pnl >= 0 ? 'text-long' : 'text-short'}`}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}</span>
    {pnlPct != null && (
      <span className={`mono tdc-pnl-pct ${pnlPct >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-pnl-pct-${t.id}`}>
        ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
      </span>
    )}
    <CaretDown size={13} className={`tdc-caret ${open ? 'rot' : ''}`} />
  </span>
  <span className="tdc-strat-line" title={stratName(t)}>{stratName(t)}</span>
</button>

      {open && (
        <div className="tdc-body" data-testid={`trade-card-body-${t.id}`}>
          <div className="tdc-ladder">
            <LevelRow label={`TP Full (${c.rr_tpf || '?'}R)`} value={t.tpf} pct={c.tpf_distance_pct} cls="lvl-tp" />
            <LevelRow label={`TP1 (${c.rr_tp1 || '?'}R)`} value={t.tp1} pct={c.tp1_distance_pct} cls="lvl-tp1" hit={t.tp1_hit} />
            <LevelRow label="Entry" value={t.entry} pct={0} cls="lvl-entry" />
            {!closed && c.current_price != null && (
              <LevelRow label="Aktueller Kurs" value={c.current_price} pct={c.price_distance_pct} cls="lvl-current" />
            )}
            {closed && <LevelRow label="Exit" value={t.exit_price} pct={c.exit_distance_pct} cls="lvl-exit" />}
            <LevelRow label={`SL${c.sl_moved ? ' (aktuell)' : ''}`} value={t.sl} pct={c.sl_distance_pct} cls="lvl-sl" />
            {c.sl_moved ? <LevelRow label="SL initial" value={t.initial_sl} pct={c.initial_sl_distance_pct} cls="lvl-sl-init" /> : null}
          </div>

          <div className="tdc-meta">
            <div className="tdc-meta-item"><span>Eröffnet</span><b className="mono">{fmtTime(t.opened_at)}</b></div>
            {closed && <div className="tdc-meta-item"><span>Geschlossen</span><b className="mono">{fmtTime(t.closed_at)}</b></div>}
            {!closed && c.current_price != null && (
              <div className="tdc-meta-item" data-testid={`trade-current-price-${t.id}`}><span>Aktueller Kurs</span><b className="mono">{c.current_price}</b></div>
            )}
            {!closed && c.unrealized_pnl != null && (
              <div className="tdc-meta-item"><span>Unrealisierter PnL</span><b className={`mono ${c.unrealized_pnl >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-unrealized-pnl-${t.id}`}>{c.unrealized_pnl >= 0 ? '+' : ''}{c.unrealized_pnl.toFixed(2)} $</b></div>
            )}
            {!closed && c.live_pnl != null && (
              <div className="tdc-meta-item"><span>Live PnL (inkl. Gebühren)</span><b className={`mono ${c.live_pnl >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-live-pnl-${t.id}`}>{c.live_pnl >= 0 ? '+' : ''}{c.live_pnl.toFixed(2)} $</b></div>
            )}
            <div className="tdc-meta-item"><span>Dauer</span><b className="mono">{fmtDur(c.duration_seconds)}</b></div>
            <div className="tdc-meta-item"><span>R-Vielfaches</span><b className={`mono ${(c.r_multiple || 0) >= 0 ? 'text-long' : 'text-short'}`}>{c.r_multiple != null ? `${c.r_multiple}R` : '—'}</b></div>
            <div className="tdc-meta-item"><span>PnL %</span><b className={`mono ${(c.pnl_pct || 0) >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-meta-pnl-pct-${t.id}`}>{c.pnl_pct != null ? fmtPct(c.pnl_pct) : '—'}</b></div>
            <div className="tdc-meta-item"><span>PnL % Kapital</span><b className={`mono ${(c.pnl_pct_capital || 0) >= 0 ? 'text-long' : 'text-short'}`}>{c.pnl_pct_capital != null ? fmtPct(c.pnl_pct_capital) : '—'}</b></div>
            <div className="tdc-meta-item"><span>Risk</span><b className="mono">{c.risk_usd ? `${c.risk_usd} $` : '—'}</b></div>
            <div className="tdc-meta-item"><span>Hebel</span><b className="mono">{t.leverage ? `${t.leverage}x` : '—'}</b></div>
            <div className="tdc-meta-item"><span>Kapital</span><b className="mono">{t.max_capital ? `${t.max_capital} $` : '—'}</b></div>
            <div className="tdc-meta-item"><span>Menge</span><b className="mono">{t.qty ?? '—'}</b></div>
            <div className="tdc-meta-item"><span>TP1 getroffen</span><b className="mono">{t.tp1_hit ? 'Ja' : 'Nein'}</b></div>
          </div>

          {(t.events || []).length > 0 && (
            <div className="tdc-timeline" data-testid={`trade-timeline-${t.id}`}>
              <div className="tdc-tl-title">VERLAUF</div>
              {t.events.map((ev, i) => (
                <div key={i} className="tdc-tl-item"><span className="tdc-tl-dot" />{ev}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const CLEAR_RANGES = [
  { key: 'hour', label: 'Letzte Stunde' },
  { key: '24h', label: 'Letzte 24 Stunden' },
  { key: '7d', label: 'Letzte 7 Tage' },
  { key: '4w', label: 'Letzte 4 Wochen' },
  { key: 'all', label: 'Gesamter Zeitraum (alles)' },
];

const PerformanceAnalytics = ({ performance, strategies = [], enabledIds = [], signals, selectedCoin, selectedStrategy, strategyOverrides = {}, strategyCoinConfigs = {}, isAdmin, onNeedAdmin, onCleared }) => {
  const [view, setView] = useState('overview');
  const [timeAnalytics, setTimeAnalytics] = useState(null);
  const [trades, setTrades] = useState([]);
  const [balance, setBalance] = useState(null);
  const [showClear, setShowClear] = useState(false);
  const [clearRange, setClearRange] = useState('24h');
  const [clearScope, setClearScope] = useState('all');
  const [clearing, setClearing] = useState(false);
  const [pnlFilter, setPnlFilter] = useState('all');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiReview, setAiReview] = useState(null);
  const [aiError, setAiError] = useState(null);

  const getCoinName = (s) => s?.replace('USDT', '') || '';
  const stratName = (t) => t?.strategy_name || strategies.find(s => s.id === t?.strategy_id)?.name || t?.strategy_id || '—';

  // Resolve the auto-trade mode of the SELECTED strategy for the SELECTED coin
  // (per-strategy-per-coin config wins, falls back to strategy-level override).
  const resolveStrategyMode = (strategyId, coin) => {
    if (!strategyId) return 'off';
    const perCoin = strategyCoinConfigs?.[strategyId]?.[coin];
    if (perCoin && perCoin.mode) return perCoin.mode; // 'live' | 'paper' | 'off'
    const override = strategyOverrides?.[strategyId];
    if (!override || !override.enabled || override.mode === 'off') return 'off';
    return override.mode || 'off';
  };

  const activeStrategyName = strategies.find(s => s.id === selectedStrategy)?.name || '—';
  const activeMode = resolveStrategyMode(selectedStrategy, selectedCoin);
  const bannerMeta = {
    live:  { cls: 'mode-live',  label: 'ECHTGELD · LIVE',     pill: 'LIVE',  head: 'AKTIV' },
    paper: { cls: 'mode-paper', label: 'SIMULATION · PAPER',  pill: 'PAPER', head: 'AKTIV' },
    off:   { cls: 'mode-off',   label: 'DEAKTIVIERT · AUS',   pill: 'AUS',   head: 'INAKTIV' },
  };
  const banner = bannerMeta[activeMode] || bannerMeta.off;

  const stratSignals = signals.filter(s => !selectedStrategy || s.strategy_id === selectedStrategy);
  const totalSignals = stratSignals.length;
  const longSignals = stratSignals.filter(s => s.type === 'LONG').length;
  const shortSignals = stratSignals.filter(s => s.type === 'SHORT').length;
  const wins = stratSignals.filter(s => s.result === 'win').length;
  const losses = stratSignals.filter(s => s.result === 'loss').length;
  const decided = wins + losses;
  const winRate = decided ? Math.round(wins / decided * 100) : 0;

  // Trades heute (opened_at seit Mitternacht Europe/Berlin, optional nach aktiver Strategie gefiltert)
  const startOfTodayMs = (() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  })();
  const tradesToday = trades.filter(t => {
    const raw = t.opened_at || t.created_at || t.entry_time || t.timestamp;
    if (!raw) return false;
    const ts = typeof raw === 'number' ? raw : new Date(raw).getTime();
    if (!Number.isFinite(ts) || ts < startOfTodayMs) return false;
    if (selectedStrategy && t.strategy_id && t.strategy_id !== selectedStrategy) return false;
    return true;
  }).length;

  const totalWins = performance.reduce((a, p) => a + (p.wins || 0), 0);
  const totalLosses = performance.reduce((a, p) => a + (p.losses || 0), 0);
  const globalDecided = totalWins + totalLosses;
  const globalWinRate = globalDecided ? Math.round(totalWins / globalDecided * 100) : 0;

  const topPerformers = performance.filter(p => p.total_signals > 0)
    .sort((a, b) => (b.win_rate || 0) - (a.win_rate || 0)).slice(0, 5);

  const loadTrades = useCallback(() => {
    fetch(`${API_URL}/api/autotrade/trades?limit=200`).then(r => r.json()).then(d => setTrades(d.trades || [])).catch(() => {});
    fetch(`${API_URL}/api/autotrade/balance`).then(r => r.json()).then(setBalance).catch(() => {});
  }, []);

  useEffect(() => {
    if (view === 'time-based' && selectedCoin) {
      fetch(`${API_URL}/api/analytics/time-based/${selectedCoin}`).then(r => r.json()).then(setTimeAnalytics).catch(() => {});
    }
    if (view === 'trades') { loadTrades(); const iv = setInterval(loadTrades, 15000); return () => clearInterval(iv); }
    // Trades werden auch in der Übersicht benötigt (für "Trades heute"-Zähler)
    if (view === 'overview') { loadTrades(); const iv = setInterval(loadTrades, 15000); return () => clearInterval(iv); }
  }, [view, selectedCoin, loadTrades]);

  // Globaler Live/Paper-Filter (obere Auswahl) für den GESAMTEN Analyse-Bereich
  const filterFn = (t) => pnlFilter === 'all' || (pnlFilter === 'live' ? t.mode === 'live' : t.mode !== 'live');
  const openTrades = trades.filter(t => t.status === 'open' && filterFn(t));
  const closedTrades = trades.filter(t => t.status === 'closed' && filterFn(t));

  // Coin-specific slices (for currently selected coin)
  const coinClosedTrades = closedTrades.filter(t => t.symbol === selectedCoin);
  const coinOpenTrades = openTrades.filter(t => t.symbol === selectedCoin);

  const pnlTotal = pnlFilter === 'all'
    ? (balance?.realized_pnl || 0)
    : closedTrades.reduce((a, t) => a + (t.realized_pnl || 0), 0);
  const coinPnl = coinClosedTrades.reduce((a, t) => a + (t.realized_pnl || 0), 0);

  // Performance per ACTIVE strategy for the SELECTED COIN
  // Show every active strategy, even without trades yet.
  const activeStrategies = strategies.filter(s => enabledIds.includes(s.id));
  const activeStratList = activeStrategies.length ? activeStrategies : strategies;

  const stratRows = activeStratList.map(strat => {
    const rowTrades = closedTrades.filter(t => t.strategy_id === strat.id && t.symbol === selectedCoin);
    const wins = rowTrades.filter(t => t.result === 'win').length;
    const losses = rowTrades.filter(t => t.result === 'loss').length;
    const decided = wins + losses;
    const pnl = rowTrades.reduce((a, t) => a + (t.realized_pnl || 0), 0);
    const openCount = openTrades.filter(t => t.strategy_id === strat.id && t.symbol === selectedCoin).length;
    return {
      id: strat.id,
      name: strat.name || strat.id,
      wins,
      losses,
      total: rowTrades.length,
      openCount,
      pnl,
      wr: decided ? Math.round((wins / decided) * 100) : 0,
    };
  }).sort((a, b) => (b.total + b.openCount) - (a.total + a.openCount));

  const openClear = () => {
    if (!isAdmin) { onNeedAdmin && onNeedAdmin(); return; }
    if (clearScope === 'coin_strategy' && !selectedStrategy) setClearScope('all');
    setShowClear(true);
  };

  const runClear = async () => {
    setClearing(true);
    try {
      const payload = { range: clearRange, scope: clearScope };
      if (clearScope !== 'all') payload.symbol = selectedCoin;
      if (clearScope === 'coin_strategy') payload.strategy_id = selectedStrategy;
      const res = await fetch(`${API_URL}/api/analytics/clear`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        const total = Object.values(data.deleted || {}).reduce((a, b) => a + b, 0);
        toast.success(`Analyse-Daten gelöscht (${total} Einträge · ${scopeLabel})`);
        setShowClear(false);
        onCleared && onCleared();
      } else if (res.status === 401) {
        toast.error('Admin-Login erforderlich');
        onNeedAdmin && onNeedAdmin();
      } else {
        toast.error('Fehler beim Löschen');
      }
    } catch {
      toast.error('Verbindungsfehler');
    } finally {
      setClearing(false);
    }
  };

  const rangeLabel = CLEAR_RANGES.find(r => r.key === clearRange)?.label || '';

  const clearScopeOptions = [
    { key: 'all', label: 'Alle Coins & Strategien', disabled: false },
    { key: 'coin', label: `Nur ${getCoinName(selectedCoin)}`, disabled: !selectedCoin },
    {
      key: 'coin_strategy',
      label: `Nur "${activeStrategyName}" bei ${getCoinName(selectedCoin)}`,
      disabled: !selectedStrategy || !selectedCoin,
      hint: !selectedStrategy ? 'Keine Strategie ausgewählt' : null,
    },
  ];
  const scopeLabel = clearScope === 'coin'
    ? `nur ${getCoinName(selectedCoin)}`
    : clearScope === 'coin_strategy'
      ? `nur "${activeStrategyName}" bei ${getCoinName(selectedCoin)}`
      : 'alle Coins & Strategien';

  const runAiReview = async () => {
    setAiLoading(true);
    setAiError(null);
    setAiReview(null);
    try {
      const res = await fetch(`${API_URL}/api/analytics/ai-review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(selectedStrategy ? { strategy_id: selectedStrategy } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = data?.detail || `Fehler ${res.status}`;
        setAiError(msg);
        toast.error(`KI-Analyse fehlgeschlagen: ${msg}`);
      } else {
        setAiReview(data.review || 'Keine Antwort erhalten.');
        toast.success('KI-Analyse fertig');
      }
    } catch (e) {
      setAiError('Verbindungsfehler zum Backend');
      toast.error('Verbindungsfehler');
    } finally {
      setAiLoading(false);
    }
  };

  return (
    <div className="performance-analytics" data-testid="performance-analytics">
      <div className="analytics-header">
        <div><h3>ANALYSE</h3><div className="analytics-subtitle">Live Statistics</div></div>
        <button className="clear-data-btn" onClick={openClear} data-testid="clear-analytics-btn" title="Analyse-Daten löschen">
          <Trash size={15} weight="bold" />
        </button>
      </div>

      <div className="view-switcher">
        <button className={`view-btn ${view === 'overview' ? 'active' : ''}`} onClick={() => setView('overview')} data-testid="view-overview"><ChartBar size={14} />Übersicht</button>
        <button className={`view-btn ${view === 'trades' ? 'active' : ''}`} onClick={() => setView('trades')} data-testid="view-trades"><Lightning size={14} />Trades</button>
        <button className={`view-btn ${view === 'time-based' ? 'active' : ''}`} onClick={() => setView('time-based')} data-testid="view-time-based"><Clock size={14} />Zeit</button>
      </div>

      {view === 'overview' && (
        <>
          <div className="analytics-section">
            <div className="section-title">HEUTE (aktive Strategie)</div>
            <div className="stats-grid">
              <div className="stat-card"><div className="stat-icon"><Target size={20} className="text-warning" /></div><div className="stat-content"><div className="stat-value mono">{totalSignals}</div><div className="stat-label">Signale</div></div></div>
              <div className="stat-card"><div className="stat-icon"><TrendUp size={20} className="text-long" /></div><div className="stat-content"><div className="stat-value mono text-long">{longSignals}</div><div className="stat-label">Long</div></div></div>
              <div className="stat-card"><div className="stat-icon"><TrendDown size={20} className="text-short" /></div><div className="stat-content"><div className="stat-value mono text-short">{shortSignals}</div><div className="stat-label">Short</div></div></div>
              <div className="stat-card" data-testid="stat-trades-today"><div className="stat-icon"><Lightning size={20} className="text-warning" /></div><div className="stat-content"><div className="stat-value mono">{tradesToday}</div><div className="stat-label">Trades</div></div></div>
              <div className="stat-card"><div className="stat-icon"><CheckCircle size={20} className="text-long" /></div><div className="stat-content"><div className="stat-value mono">{winRate}%</div><div className="stat-label">Win-Rate ({decided})</div></div></div>
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">GESAMT-ANALYSE (dauerhaft)</div>
            <div className="global-stats">
              <div className="global-stat"><span className="text-long mono">{totalWins}</span><span className="text-muted">Wins</span></div>
              <div className="global-stat"><span className="text-short mono">{totalLosses}</span><span className="text-muted">Losses</span></div>
              <div className="global-stat"><span className="mono" style={{ color: globalWinRate >= 50 ? '#00FF66' : '#FF3366' }}>{globalWinRate}%</span><span className="text-muted">Win-Rate</span></div>
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">TOP COINS (Win-Rate)</div>
            <div className="top-coins-list">
              {topPerformers.length === 0 && <div className="no-data">Noch keine Daten</div>}
              {topPerformers.map((coin, i) => (
                <div key={coin.symbol} className="top-coin-item" data-testid={`top-coin-${coin.symbol}`}>
                  <div className="coin-rank">{i + 1}</div>
                  <div className="coin-info"><div className="coin-name mono">{getCoinName(coin.symbol)}</div>
                    <div className="coin-signals"><span className="text-long mono">{coin.long_signals}</span><span className="text-muted">/</span><span className="text-short mono">{coin.short_signals}</span></div></div>
                  <div className="coin-crv"><div className="crv-label">WR</div><div className="crv-value mono" style={{ color: (coin.win_rate || 0) >= 50 ? '#00FF66' : '#FF3366' }}>{(coin.win_rate || 0).toFixed(0)}%</div></div>
                </div>
              ))}
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">LETZTE SIGNALE</div>
            <div className="recent-signals-list">
              {stratSignals.slice(0, 6).map((s, i) => (
                <div key={i} className="recent-signal-item">
                  <span className={`badge ${s.type === 'LONG' ? 'badge-long' : 'badge-short'}`}>{s.signal_class === 'PRE_SIGNAL' ? 'PRE-' : ''}{s.type}</span>
                  <span className="mono text-secondary">{getCoinName(s.symbol)}</span>
                  {s.result && <span className={s.result === 'win' ? 'text-long' : 'text-short'} style={{ fontSize: '10px' }}>{s.result === 'win' ? '✓' : '✗'}</span>}
                  <span className="mono text-muted" style={{ fontSize: '10px', marginLeft: 'auto' }}>{new Date(s.timestamp).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' })}</span>
                </div>
              ))}
              {stratSignals.length === 0 && <div className="no-data">Keine Signale heute</div>}
            </div>
          </div>

          <div className="analytics-section ai-review-section">
            <button
              className="ai-review-btn"
              onClick={runAiReview}
              disabled={aiLoading}
              data-testid="ai-review-start-btn"
            >
              <Sparkle size={14} weight="bold" />
              {aiLoading ? 'Analysiere...' : 'KI-Analyse starten'}
            </button>
            {aiLoading && (
              <div className="ai-review-loading" data-testid="ai-review-loading">
                <div className="ai-spinner" /> Coach denkt nach...
              </div>
            )}
            {aiError && !aiLoading && (
              <div className="ai-review-error" data-testid="ai-review-error">
                <Warning size={14} weight="bold" /> {aiError}
              </div>
            )}
            {aiReview && !aiLoading && (
              <div className="ai-review-card" data-testid="ai-review-result">
                <pre className="ai-review-text">{aiReview}</pre>
              </div>
            )}
          </div>
        </>
      )}

      {view === 'trades' && (
        <>
          <div className={`mode-banner ${banner.cls}`} data-testid="active-mode-banner">
            <div className="mode-banner-dot" />
            <div className="mode-banner-text">
              <span className="mode-banner-label">{banner.head} · {activeStrategyName} · {getCoinName(selectedCoin)}</span>
              <span className="mode-banner-value">{banner.label}</span>
            </div>
            <span className={`mode-pill ${banner.cls}`}>{banner.pill}</span>
          </div>

          <div className="pnl-filter" data-testid="pnl-filter">
            <button className={`pnl-filter-btn ${pnlFilter === 'all' ? 'active' : ''}`} onClick={() => setPnlFilter('all')} data-testid="pnl-filter-all">Alle</button>
            <button className={`pnl-filter-btn live ${pnlFilter === 'live' ? 'active' : ''}`} onClick={() => setPnlFilter('live')} data-testid="pnl-filter-live">Live</button>
            <button className={`pnl-filter-btn paper ${pnlFilter === 'paper' ? 'active' : ''}`} onClick={() => setPnlFilter('paper')} data-testid="pnl-filter-paper">Paper</button>
          </div>

          <div className="analytics-section">
            <div className="stats-grid">
              <div className="stat-card" data-testid="pnl-total-card">
                <div className="stat-content">
                  <div className="stat-value mono" style={{ color: pnlTotal >= 0 ? '#00FF66' : '#FF3366' }}>
                    {pnlTotal.toFixed(2)}
                  </div>
                  <div className="stat-label">PnL Gesamt (USDT)</div>
                </div>
              </div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{openTrades.length}</div><div className="stat-label">Offen</div></div></div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{closedTrades.length}</div><div className="stat-label">Geschlossen</div></div></div>
            </div>
            <div className="stats-grid" style={{ marginTop: 8 }}>
              <div className="stat-card stat-card-coin" data-testid="pnl-coin-card">
                <div className="stat-content">
                  <div className="stat-value mono" style={{ color: coinPnl >= 0 ? '#00FF66' : '#FF3366' }}>
                    {coinPnl.toFixed(2)}
                  </div>
                  <div className="stat-label">PnL {getCoinName(selectedCoin)} (USDT)</div>
                </div>
              </div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{coinOpenTrades.length}</div><div className="stat-label">Offen · {getCoinName(selectedCoin)}</div></div></div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{coinClosedTrades.length}</div><div className="stat-label">Geschl. · {getCoinName(selectedCoin)}</div></div></div>
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">
              PERFORMANCE JE STRATEGIE · {getCoinName(selectedCoin)}{pnlFilter !== 'all' ? ` · ${pnlFilter === 'live' ? 'LIVE' : 'PAPER'}` : ''}
            </div>
            {stratRows.length === 0 && <div className="no-data">Keine aktiven Strategien</div>}
            {stratRows.map(s => {
              const empty = s.total === 0 && s.openCount === 0;
              return (
                <div key={s.id} className={`strat-perf-row ${empty ? 'strat-perf-empty' : ''}`} data-testid={`strat-perf-${s.id}`}>
                  <div className="strat-perf-name" title={s.name}>{s.name}</div>
                  <div className="strat-perf-stats">
                    {s.openCount > 0 && <span className="mono text-warning" title="offene Trades">{s.openCount}○</span>}
                    <span className="text-long mono">{s.wins}W</span>
                    <span className="text-short mono">{s.losses}L</span>
                    <span className="mono" style={{ color: (s.wins + s.losses) === 0 ? '#5C6070' : (s.wr >= 50 ? '#00FF66' : '#FF3366') }}>
                      {(s.wins + s.losses) === 0 ? '—' : `${s.wr}%`}
                    </span>
                    <span className={`mono ${s.pnl === 0 ? 'text-muted' : (s.pnl >= 0 ? 'text-long' : 'text-short')}`}>
                      {s.pnl.toFixed(2)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="analytics-section">
            <div className="section-title">OFFENE TRADES <span className="sec-count">{openTrades.length}</span></div>
            {openTrades.length === 0 && <div className="no-data">Keine offenen Trades</div>}
            {openTrades.map(t => (
              <TradeDetailCard key={t.id} t={t} stratName={stratName} getCoinName={getCoinName} />
            ))}
          </div>

          <div className="analytics-section">
            <div className="section-title">GESCHLOSSENE TRADES <span className="sec-count">{closedTrades.length}</span></div>
            {closedTrades.length === 0 && <div className="no-data">Keine</div>}
            {closedTrades.slice(0, 30).map(t => (
              <TradeDetailCard key={t.id} t={t} stratName={stratName} getCoinName={getCoinName} />
            ))}
          </div>
        </>
      )}

      {view === 'time-based' && (
        <div className="analytics-section">
          <div className="section-title">ZEIT-ANALYSE: {getCoinName(selectedCoin)}</div>
          {!timeAnalytics || (timeAnalytics.time_analytics || []).length === 0 ? (
            <div className="no-data">Noch keine Zeit-Analyse. Sobald Signale kommen, siehst du hier die besten Stunden.</div>
          ) : (
            <div className="time-section">
              <div className="time-subtitle text-long">BESTE ZEITEN</div>
              {(timeAnalytics.best_hours || []).slice(0, 5).map((stat, i) => (
                <div key={i} className="time-item">
                  <div className="time-info"><span className="mono">{String(stat.hour).padStart(2, '0')}:00</span><span className="text-muted"> · {stat.weekday}</span></div>
                  <div className="time-stats"><span className="mono text-long">{stat.win_rate.toFixed(0)}% WR</span><span className="text-muted">·</span><span className="mono">{stat.total_signals}x</span></div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {showClear && (
        <div className="clear-overlay" onClick={() => !clearing && setShowClear(false)}>
          <div className="clear-modal" onClick={e => e.stopPropagation()} data-testid="clear-analytics-modal">
            <div className="clear-modal-header">
              <Trash size={18} weight="bold" />
              <h4>Analyse-Daten löschen</h4>
            </div>
            <p className="clear-modal-sub">Wähle, was gelöscht werden soll – und für welchen Zeitraum (wie beim Browser-Verlauf).</p>

            <div className="clear-section-label">WAS LÖSCHEN?</div>
            <div className="clear-ranges clear-scopes">
              {clearScopeOptions.map(o => (
                <label
                  key={o.key}
                  className={`clear-range ${clearScope === o.key ? 'active' : ''} ${o.key === 'all' ? 'danger' : ''} ${o.disabled ? 'disabled' : ''}`}
                  title={o.disabled && o.hint ? o.hint : undefined}
                  data-testid={`clear-scope-${o.key}`}
                >
                  <input type="radio" name="clear-scope" value={o.key} checked={clearScope === o.key} disabled={o.disabled} onChange={() => setClearScope(o.key)} />
                  <span>{o.label}{o.disabled && o.hint ? <em className="clear-scope-hint"> · {o.hint}</em> : null}</span>
                </label>
              ))}
            </div>

            <div className="clear-section-label">ZEITRAUM</div>
            <div className="clear-ranges">
              {CLEAR_RANGES.map(r => (
                <label key={r.key} className={`clear-range ${clearRange === r.key ? 'active' : ''} ${r.key === 'all' && clearScope === 'all' ? 'danger' : ''}`} data-testid={`clear-range-${r.key}`}>
                  <input type="radio" name="clear-range" value={r.key} checked={clearRange === r.key} onChange={() => setClearRange(r.key)} />
                  <span>{r.label}</span>
                </label>
              ))}
            </div>

            <div className="clear-summary" data-testid="clear-summary">
              Es wird gelöscht: <b>{rangeLabel}</b> · <b>{scopeLabel}</b>
            </div>
            <div className="clear-warn"><Warning size={14} weight="bold" /> Gelöschte Signale &amp; Statistiken können nicht wiederhergestellt werden.</div>
            <div className="clear-actions">
              <button className="clear-cancel" onClick={() => setShowClear(false)} disabled={clearing} data-testid="clear-cancel-btn">Abbrechen</button>
              <button className="clear-confirm" onClick={runClear} disabled={clearing} data-testid="clear-confirm-btn">
                {clearing ? 'Lösche...' : `Löschen (${scopeLabel})`}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default PerformanceAnalytics;
