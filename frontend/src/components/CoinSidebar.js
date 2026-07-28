import React from 'react';
import { TrendUp, TrendDown, Circle, Bell, BellSlash, Lightning } from '@phosphor-icons/react';
import './CoinSidebar.css';

const GROUPS = [
  { name: 'TOP 10 COINS', items: ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "POLUSDT"] },
  { name: 'OTHER', items: ["GOLD", "SILVER", "OIL"] },
];

const CoinSidebar = ({ selectedCoin, onSelectCoin, performance, notifications = {}, onToggleNotification,
                       ruleStates = {}, selectedStrategy, autotradeCoins = {}, onToggleAutoTrade }) => {
  const getCoinName = (s) => (["GOLD", "SILVER", "OIL"].includes(s) ? s : s.replace('USDT', ''));
  const getPerf = (s) => performance.find(p => p.symbol === s) || {};

  const renderItem = (coin) => {
    const perf = getPerf(coin);
    const isSelected = coin === selectedCoin;
    const hasSignals = perf.total_signals > 0;
    const notifyOn = notifications[coin] !== false;
    const state = ruleStates[coin]?.[selectedStrategy];
    const bias = state?.bias;
    const autoOn = autotradeCoins[coin]?.enabled;

    let dotClass = 'coin-indicator';
    if (bias === 'LONG') dotClass = 'coin-indicator-long';
    else if (bias === 'SHORT') dotClass = 'coin-indicator-short';
    else if (hasSignals) dotClass = 'coin-indicator-active';

    return (
      <div key={coin} className={`coin-item ${isSelected ? 'coin-item-selected' : ''}`} onClick={() => onSelectCoin(coin)} data-testid={`coin-item-${coin}`}>
        <div className="coin-header">
          <div className="coin-name">
            <Circle size={8} weight="fill" className={dotClass} />
            <span className="mono">{getCoinName(coin)}</span>
            {state && (
              <span className="coin-bias-count mono">{Math.max(state.long_count || 0, state.short_count || 0)}/{state.rules_total || 0}</span>
            )}
          </div>
          <div className="coin-header-right">
            <button
              className={`auto-toggle ${autoOn ? 'auto-on' : ''}`}
              onClick={(e) => { e.stopPropagation(); onToggleAutoTrade && onToggleAutoTrade(coin, !autoOn); }}
              title={autoOn ? 'Auto-Trade AKTIV – klicken zum Deaktivieren' : 'Auto-Trade INAKTIV – klicken zum Aktivieren'}
              data-testid={`autotrade-btn-${coin}`}
            >
              <Lightning size={14} weight={autoOn ? 'fill' : 'regular'} />
            </button>
            <button
              className={`notify-toggle ${notifyOn ? 'notify-on' : 'notify-off'}`}
              onClick={(e) => { e.stopPropagation(); onToggleNotification && onToggleNotification(coin); }}
              title={notifyOn ? 'Alerts an' : 'Alerts aus'}
              data-testid={`notify-toggle-${coin}`}
            >
              {notifyOn ? <Bell size={14} weight="fill" /> : <BellSlash size={14} />}
            </button>
          </div>
        </div>

        {hasSignals && (
          <div className="coin-stats">
            <div className="coin-stat"><TrendUp size={12} className="text-long" /><span className="mono text-secondary">{perf.long_signals || 0}</span></div>
            <div className="coin-stat"><TrendDown size={12} className="text-short" /><span className="mono text-secondary">{perf.short_signals || 0}</span></div>
            <div className="coin-stat"><span className="text-muted">WR</span><span className="mono text-secondary">{perf.win_rate?.toFixed(0) || 0}%</span></div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="coin-sidebar" data-testid="coin-sidebar">
      <div className="sidebar-header"><h3>MARKETS</h3><div className="sidebar-subtitle">Live Scanner</div></div>
      {GROUPS.map(group => (
        <div key={group.name} className="coin-group">
          <div className="coin-group-title" data-testid={`group-${group.name}`}>{group.name}</div>
          <div className="coin-list">{group.items.map(renderItem)}</div>
        </div>
      ))}
    </div>
  );
};

export default CoinSidebar;
