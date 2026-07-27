import React, { useState, useEffect, useCallback } from 'react';
import { Clock, Gear, ChartLineUp, Wallet, TrendUp, TrendDown, Lock, LockOpen, Trophy, ClockCounterClockwise, MagicWand } from '@phosphor-icons/react';
import { authHeaders } from '../auth';
import CapitalModal from './CapitalModal';
import './Header.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const BalanceWidget = () => {
  const [bal, setBal] = useState(null);
  // Statt eines gemeinsamen Modal-States pflegen wir den Scope-Lock explizit.
  // null = geschlossen, 'live' oder 'paper' = geöffnet mit fixem Scope.
  const [capitalScope, setCapitalScope] = useState(null);

  const load = useCallback(async () => {
    try {
      const d = await fetch(`${API_URL}/api/autotrade/balance`).then(r => r.json());
      setBal(d);
    } catch (_) { /* ignore */ }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 15000);
    return () => clearInterval(iv);
  }, [load]);

  if (!bal) {
    // Skeleton während des initialen Loads: identisches Layout, damit der
    // Header nicht "nachspringt", sobald die Balance-Daten eintreffen.
    return (
      <div className="balance-widget-wrapper" data-testid="bitunix-balance-skeleton">
        <div className="balance-widget bw-skeleton" aria-busy="true">
          <div className="bw-mode live">
            <Wallet size={14} weight="fill" />
            LIVE
          </div>
          <div className="bw-stack">
            <span className="bw-usdt-label">USDT</span>
            <span className="bw-primary-value mono">—</span>
            <span className="bw-sub-line">
              <span className="bw-sub-label">frei</span>
              <span className="mono">—</span>
            </span>
          </div>
        </div>
        <div className="paper-overlay bw-skeleton" aria-busy="true">
          <div className="paper-overlay-mode">
            <Wallet size={12} weight="fill" />
            PAPER
          </div>
          <div className="overlay-stack">
            <span className="bw-usdt-label">PnL</span>
            <div className="paper-overlay-pnl">
              <span className="bw-primary-value bw-value-muted mono">—</span>
            </div>
            <span className="bw-sub-line">
              <span className="bw-sub-label">frei</span>
              <span className="mono">—</span>
            </span>
          </div>
        </div>
      </div>
    );
  }
  const isLive = bal.mode === 'live';
  const pnl = bal.realized_pnl || 0;
  const pnlPos = pnl >= 0;

  // Paper overlay data
  const paperPnl = bal.paper_pnl ?? null;
  const paperPnlPos = (paperPnl || 0) >= 0;

  const liveAlloc = bal.allocation?.live;
  const paperAlloc = bal.allocation?.paper;
  const alloc = isLive ? liveAlloc : paperAlloc;

  // Hauptwidget öffnet immer mit dem aktuellen Modus als gesperrtem Scope.
  const openMainCapital = () => setCapitalScope(isLive ? 'live' : 'paper');
  const openPaperCapital = (e) => {
    e.stopPropagation();
    setCapitalScope('paper');
  };
  const openLiveCapital = (e) => {
    e.stopPropagation();
    setCapitalScope('live');
  };

  return (
    <div className="balance-widget-wrapper">
      <div className="balance-widget bw-clickable" data-testid="bitunix-balance-widget"
        onClick={openMainCapital}
        title={isLive ? 'Live-Kapital anpassen' : 'Paper-Kapital anpassen'}
        role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') openMainCapital(); }}>
        <div className={`bw-mode ${isLive ? 'live' : 'paper'}`} data-testid="bw-mode">
          <Wallet size={14} weight="fill" />
          {isLive ? 'LIVE' : 'PAPER'}
        </div>
        {isLive ? (
          bal.bitunix_configured ? (
            <div className="bw-stack" data-testid="bw-live-stack">
              <span className="bw-usdt-label">USDT</span>
              <span className="bw-primary-value mono" data-testid="bw-total">
                {bal.margin_balance != null ? Number(bal.margin_balance).toFixed(2) : (bal.bitunix_error ? 'API-Fehler' : '—')}
              </span>
              {alloc?.free != null ? (
                <span className="bw-sub-line" data-testid="bw-alloc">
                  <span className="bw-sub-label">frei</span>
                  <span className="mono">{Number(alloc.free).toFixed(2)}</span>
                </span>
              ) : (
                <span className="bw-sub-line" data-testid="bw-free">
                  <span className="bw-sub-label">Kapital</span>
                  <span className="mono">{bal.available != null ? Number(bal.available).toFixed(2) : '—'}</span>
                </span>
              )}
            </div>
          ) : (
            <div className="bw-item bw-warn" data-testid="bw-unconfigured">Bitunix nicht konfiguriert</div>
          )
        ) : (
          <div className="bw-stack" data-testid="bw-paper-stack">
            <span className="bw-usdt-label">PnL</span>
            <span className={`bw-primary-value mono ${pnlPos ? 'pos' : 'neg'}`}>
              {pnlPos ? <TrendUp size={13} weight="bold" /> : <TrendDown size={13} weight="bold" />}
              {pnl.toFixed(2)}
            </span>
            {alloc?.free != null && (
              <span className="bw-sub-line" data-testid="bw-alloc">
                <span className="bw-sub-label">frei</span>
                <span className="mono">{Number(alloc.free).toFixed(2)}</span>
              </span>
            )}
          </div>
        )}
      </div>

      {/* Paper Badge - im Live-Modus sichtbar, klickbar für Paper-Kapital. */}
      {isLive && (
        <div className="paper-overlay bw-clickable" data-testid="paper-overlay"
          onClick={openPaperCapital}
          title="Paper-Kapital anpassen"
          role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') openPaperCapital(e); }}>
          <div className="paper-overlay-mode">
            <Wallet size={12} weight="fill" />
            PAPER
          </div>
          <div className="overlay-stack">
            <span className="bw-usdt-label">PnL</span>
            <div className="paper-overlay-pnl">
              {paperPnl != null && paperPnl !== 0 ? (
                <span className={`bw-primary-value mono ${paperPnlPos ? 'pos' : 'neg'}`}>
                  {paperPnlPos ? <TrendUp size={13} weight="bold" /> : <TrendDown size={13} weight="bold" />}
                  {(paperPnl || 0).toFixed(2)}
                </span>
              ) : (
                <span className="bw-primary-value bw-value-muted mono">—</span>
              )}
            </div>
            {paperAlloc?.free != null && (
              <span className="bw-sub-line" data-testid="paper-overlay-free">
                <span className="bw-sub-label">frei</span>
                <span className="mono">{Number(paperAlloc.free).toFixed(2)}</span>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Live Badge - im Paper-Modus sichtbar, klickbar für Live-Kapital. */}
      {!isLive && (
        <div className="live-overlay bw-clickable" data-testid="live-overlay"
          onClick={openLiveCapital}
          title="Live-Kapital anpassen"
          role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') openLiveCapital(e); }}>
          <div className="live-overlay-mode">
            <Wallet size={12} weight="fill" />
            LIVE
          </div>
          <div className="overlay-stack">
            <span className="bw-usdt-label">USDT</span>
            <span className="bw-primary-value mono" data-testid="live-overlay-balance">
              {bal.margin_balance != null ? Number(bal.margin_balance).toFixed(2) : '—'}
            </span>
            {liveAlloc?.free != null && (
              <span className="bw-sub-line" data-testid="live-overlay-free">
                <span className="bw-sub-label">frei</span>
                <span className="mono">{Number(liveAlloc.free).toFixed(2)}</span>
              </span>
            )}
          </div>
        </div>
      )}

      {capitalScope && (
        <CapitalModal
          lockedScope={capitalScope}
          onClose={() => setCapitalScope(null)}
          onSaved={load}
        />
      )}
    </div>
  );
};

const Header = ({ sessionActive, onSettingsClick, currentSession, customSessions, activeStrategy, adminAuthed, onAdminClick, onCompareClick, onBacktestClick, onOptimizerClick }) => {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const formatTime = (date) => {
    return date.toLocaleTimeString('de-DE', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      timeZone: 'Europe/Berlin',
    });
  };

  const is24_7 = !customSessions || customSessions.length === 0;
  const enabledSessions = (customSessions || []).filter(s => s.enabled !== false);

  return (
    <header className="header" data-testid="main-header">
      <div className="header-left">
        <div className="header-brand">
          <ChartLineUp size={28} weight="bold" className="brand-icon" />
          <div className="header-brand-text">
            <h1 className="header-title">CRYPTO SCANNER</h1>
            {activeStrategy && (
              <div className="header-strategy" data-testid="active-strategy-display">
                🎯 {activeStrategy.name}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="header-right">
        {/* Uhrzeit + Session-Badge als eigener Block NEBEN den Labels (keine Überlagerung mehr) */}
        <div className="header-session" data-testid="header-session">
          <div className="session-status">
            <Clock size={18} weight="bold" />
            <span className="mono">{formatTime(currentTime)}</span>
            <span className={`badge ${sessionActive ? 'badge-active' : 'badge-inactive'}`} data-testid="session-status-badge">
              {sessionActive
                ? (currentSession ? `${currentSession.toUpperCase()} · ACTIVE` : 'TRADING ACTIVE')
                : 'OUTSIDE SESSIONS'}
            </span>
          </div>
          {!is24_7 && (
            <div className="session-times">
              {enabledSessions.length === 0 ? (
                <span className="text-muted">Keine aktiven Sessions</span>
              ) : (
                enabledSessions.map((s, i) => (
                  <span key={i} className="text-muted">
                    {i > 0 && <span style={{ margin: '0 4px' }}>|</span>}
                    {s.name}: {s.start}-{s.end}
                  </span>
                ))
              )}
            </div>
          )}
        </div>
        <BalanceWidget />
        <button className="btn" onClick={onCompareClick} title="Strategie-Vergleich" data-testid="compare-strategies-button">
          <Trophy size={20} weight="bold" />
        </button>
        <button className="btn" onClick={onBacktestClick} title="Backtester (historische Daten, alle Timeframes)" data-testid="backtester-button">
          <ClockCounterClockwise size={20} weight="bold" />
        </button>
        <button className="btn" onClick={onOptimizerClick} title="Strategie-Optimizer (Parameter & Discovery)" data-testid="optimizer-button">
          <MagicWand size={20} weight="bold" />
        </button>
        <button
          className={`btn btn-admin ${adminAuthed ? 'is-admin' : ''}`}
          onClick={onAdminClick}
          title={adminAuthed ? 'Admin abmelden' : 'Admin-Login'}
          aria-label={adminAuthed ? 'Admin abmelden' : 'Admin-Login'}
          data-testid="admin-lock-button"
        >
          {adminAuthed
            ? <LockOpen size={20} weight="bold" />
            : <Lock size={20} weight="bold" />}
        </button>
        <button className="btn" onClick={onSettingsClick} data-testid="settings-button">
          <Gear size={20} weight="bold" />
        </button>
      </div>
    </header>
  );
};

export default Header;
