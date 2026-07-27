import React, { useState, useEffect } from 'react';
import SafeOverlay from './SafeOverlay';
import { X, TelegramLogo, Lightning, ChartLineUp, Plus, Trash, Sliders, PauseCircle, PlayCircle, Power } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import TIMEFRAMES from '../constants/timeframes';
import './SettingsPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const SettingsPanel = ({ onClose, focusStrategy, mode = 'all', controlState, onControlChanged }) => {
  // mode: 'all' = alle Tabs, 'general' = Steuerung + Telegram, 'strategy' = Strategie + Zeitfenster
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const defaultTab = mode === 'general' ? 'control' : 'strategy';
  const [activeTab, setActiveTab] = useState(defaultTab); // strategy, sessions, control, telegram
  const [settings, setSettings] = useState({
    custom_sessions: [],
    pre_signal_enabled: true,
    active_strategy: 'scalping_4_rules',
    strategy_params: {},
    coin_params: {},
    strategy_timeframes: {},
  });
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [paramCoin, setParamCoin] = useState(''); // '' = Global, else per-coin override
  const [sessionScope, setSessionScope] = useState('global'); // 'global' or strategy_id
  const [busy, setBusy] = useState(false);
  const [defDirty, setDefDirty] = useState(false);
  const importParamsRef = React.useRef(null);
  const ALL_COINS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","POLUSDT","GOLD","SILVER","OIL"];

  useEffect(() => {
    Promise.all([
      fetch(`${API_URL}/api/settings`).then(r => r.json()),
      fetch(`${API_URL}/api/strategies`).then(r => r.json())
    ])
      .then(([settingsData, strategiesData]) => {
        setSettings({
          custom_sessions: settingsData.custom_sessions || [],
          strategy_sessions: settingsData.strategy_sessions || {},
          pre_signal_enabled: settingsData.pre_signal_enabled !== false,
          active_strategy: settingsData.active_strategy || 'scalping_4_rules',
          strategy_params: settingsData.strategy_params || {},
          coin_params: settingsData.coin_params || {},
          strategy_timeframes: settingsData.strategy_timeframes || {},
        });
        setStrategies(strategiesData.strategies || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const saveSettings = async (updatedSettings) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich zum Speichern'); return; }
    setSaving(true);
    try {
      const response = await fetch(`${API_URL}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(updatedSettings)
      });

      if (response.status === 401) {
        toast.error('Nicht autorisiert – bitte als Admin anmelden');
        return;
      }
      if (response.ok) {
        const data = await response.json();
        setSettings(prev => ({
          ...prev,
          custom_sessions: data.settings.custom_sessions || [],
          strategy_sessions: data.settings.strategy_sessions || {},
          pre_signal_enabled: data.settings.pre_signal_enabled !== false,
          active_strategy: data.settings.active_strategy || 'scalping_4_rules',
          strategy_params: data.settings.strategy_params || {},
          coin_params: data.settings.coin_params || {},
          strategy_timeframes: data.settings.strategy_timeframes || {},
        }));
        toast.success('Gespeichert');
      } else {
        toast.error('Fehler beim Speichern');
      }
    } catch (error) {
      toast.error('Verbindungsfehler beim Speichern');
    } finally {
      setSaving(false);
    }
  };

  // ---- Control State Toggle (same API as Header) ----
  const toggleControl = async (kind) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (busy) return;
    setBusy(true);
    try {
      const path = kind === 'trades' ? 'stop-trades' : 'stop-signals';
      const r = await fetch(`${API_URL}/api/control/${path}`, {
        method: 'POST', headers: { ...authHeaders() },
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      if (kind === 'trades') {
        toast.success(d.trades_paused
          ? `Trades gestoppt${d.closed_trades ? ` – ${d.closed_trades} Bot-Trade(s) geschlossen` : ''}`
          : 'Trades wieder aktiv');
      } else {
        toast.success(d.signals_paused ? 'Signals gestoppt' : 'Signals wieder aktiv');
      }
      onControlChanged && onControlChanged();
    } catch (e) {
      toast.error('Fehler: ' + (e.message || 'unbekannt'));
    } finally {
      setBusy(false);
    }
  };

  const updateStrategyTimeframe = (strategyId, tf) => {
    const tfs = { ...(settings.strategy_timeframes || {}), [strategyId]: tf };
    setSettings({ ...settings, strategy_timeframes: tfs });
    saveSettings({ strategy_timeframes: tfs });
    toast.success(`Timeframe ${tf} gespeichert – gilt für Signale, Paper & Live`);
  };

  // ---- Custom/Discovery-Strategien: Regeln & Indikator-Perioden dauerhaft anpassen ----
  const updateDefinition = (strategyId, mut) => {
    setStrategies(prev => prev.map(s => (s.id === strategyId
      ? { ...s, definition: mut({ ...(s.definition || {}) }) } : s)));
    setDefDirty(true);
  };

  const updateDefRuleValue = (strategyId, side, idx, value) =>
    updateDefinition(strategyId, def => ({
      ...def,
      [side]: (def[side] || []).map((r, i) => (i === idx ? { ...r, value } : r)),
    }));

  const updateDefIndicator = (strategyId, key, value) =>
    updateDefinition(strategyId, def => ({
      ...def, indicators: { ...(def.indicators || {}), [key]: value },
    }));

  const saveDefinition = async (strategyId) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const s = strategies.find(x => x.id === strategyId);
    if (!s?.definition) return;
    try {
      const res = await fetch(`${API_URL}/api/strategies/custom`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(s.definition),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(d.detail || 'Speichern fehlgeschlagen'); return; }
      setDefDirty(false);
      toast.success('Strategie-Regeln gespeichert – gilt für Signale, Paper & Live');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const updateStrategyParam = (strategyId, paramKey, value) => {
    const v = parseFloat(value);
    if (paramCoin) {
      const cp = settings.coin_params || {};
      const stratCp = cp[strategyId] || {};
      const coinCp = { ...(stratCp[paramCoin] || {}), [paramKey]: v };
      const newCp = { ...cp, [strategyId]: { ...stratCp, [paramCoin]: coinCp } };
      setSettings({ ...settings, coin_params: newCp });
    } else {
      const currentParams = settings.strategy_params[strategyId] || {};
      const newParams = { ...settings.strategy_params, [strategyId]: { ...currentParams, [paramKey]: v } };
      setSettings({ ...settings, strategy_params: newParams });
    }
  };

  const commitParams = (strategyId) => {
    if (paramCoin) saveSettings({ coin_params: settings.coin_params });
    else saveSettings({ strategy_params: settings.strategy_params });
  };

  const resetStrategyParams = (strategyId) => {
    if (paramCoin) {
      const cp = { ...(settings.coin_params || {}) };
      if (cp[strategyId]) { delete cp[strategyId][paramCoin]; }
      setSettings({ ...settings, coin_params: cp });
      saveSettings({ coin_params: cp });
      toast.success(`${paramCoin} Parameter zurückgesetzt`);
    } else {
      const newParams = { ...settings.strategy_params };
      delete newParams[strategyId];
      setSettings({ ...settings, strategy_params: newParams });
      saveSettings({ strategy_params: newParams });
      toast.success('Parameter auf Standard zurückgesetzt');
    }
  };

  const getCurrentParamValue = (strategyId, paramKey, defaultValue) => {
    const globalVal = settings.strategy_params[strategyId]?.[paramKey];
    if (paramCoin) {
      const coinVal = settings.coin_params?.[strategyId]?.[paramCoin]?.[paramKey];
      return coinVal ?? globalVal ?? defaultValue;
    }
    return globalVal ?? defaultValue;
  };

  // Sessions handlers
  const togglePreSignal = (value) => {
    setSettings({ ...settings, pre_signal_enabled: value });
    saveSettings({ pre_signal_enabled: value });
  };

  // Sessions handlers – arbeiten je nach Scope auf globalen oder
  // strategie-eigenen Zeitfenstern (strategy_sessions[strategyId])
  const isGlobalScope = sessionScope === 'global';
  const scopedSessions = isGlobalScope
    ? settings.custom_sessions
    : (settings.strategy_sessions?.[sessionScope] || []);

  const setScopedSessions = (list, save = true) => {
    if (isGlobalScope) {
      setSettings(prev => ({ ...prev, custom_sessions: list }));
      if (save) saveSettings({ custom_sessions: list });
    } else {
      const ss = { ...(settings.strategy_sessions || {}) };
      if (list.length) ss[sessionScope] = list;
      else delete ss[sessionScope];
      setSettings(prev => ({ ...prev, strategy_sessions: ss }));
      if (save) saveSettings({ strategy_sessions: ss });
    }
  };

  const addSession = () => {
    const newSession = {
      start: "09:00", end: "12:00",
      name: `Session ${scopedSessions.length + 1}`,
      enabled: true
    };
    setScopedSessions([...scopedSessions, newSession]);
  };

  const removeSession = (index) => {
    setScopedSessions(scopedSessions.filter((_, i) => i !== index));
  };

  const updateSession = (index, field, value) => {
    const updated = [...scopedSessions];
    updated[index] = { ...updated[index], [field]: value };
    setScopedSessions(updated, false);
  };

  const commitSessionUpdate = () => setScopedSessions([...scopedSessions]);

  const toggleSession = (index) => {
    const updated = [...scopedSessions];
    updated[index] = { ...updated[index], enabled: !updated[index].enabled };
    setScopedSessions(updated);
  };

  const enable24_7 = () => {
    setScopedSessions([]);
    toast.success(isGlobalScope ? '24/7 Modus aktiviert' : 'Strategie folgt jetzt dem globalen Zeitfenster');
  };

  const restoreDefaults = () => {
    setScopedSessions([
      { start: "09:00", end: "12:00", name: "London", enabled: true },
      { start: "15:30", end: "18:30", name: "US", enabled: true }
    ]);
  };

  const handleTestTelegram = async () => {
    setTesting(true);
    try {
      const response = await fetch(`${API_URL}/api/telegram/test`, { method: 'POST', headers: { ...authHeaders() } });
      if (response.ok) toast.success('Telegram Test erfolgreich!');
      else if (response.status === 401) toast.error('Admin-Login erforderlich');
      else toast.error('Fehler');
    } catch {
      toast.error('Verbindungsfehler');
    } finally {
      setTesting(false);
    }
  };

  const activeStrategy = strategies.find(s => s.id === focusStrategy)
    || strategies.find(s => s.id === settings.active_strategy)
    || strategies[0];
  const is24_7 = scopedSessions.length === 0;

  // ---- Komplettes Strategie-Backup direkt aus dem ⚙-Panel ----
  const exportStrategyBackup = async () => {
    if (!activeStrategy) return;
    try {
      const res = await fetch(`${API_URL}/api/strategies/${activeStrategy.id}/export`);
      if (!res.ok) { toast.error('Export fehlgeschlagen'); return; }
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      const safe = (activeStrategy.name || activeStrategy.id).replace(/[^a-z0-9äöüß_-]+/gi, '_');
      a.download = `strategie-backup-${safe}-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success(`"${activeStrategy.name}" komplett exportiert (inkl. aller Parameter & Trade-Einstellungen)`);
    } catch { toast.error('Verbindungsfehler beim Export'); }
  };

  const importStrategyBackup = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); e.target.value = ''; return; }
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const d = JSON.parse(reader.result);
        if (d.type !== 'strategy_backup') { toast.error('Keine gültige Strategie-Backup-Datei'); return; }
        const res = await fetch(`${API_URL}/api/strategies/import`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify(d),
        });
        const out = await res.json();
        if (!res.ok) { toast.error(out.detail || 'Import fehlgeschlagen'); return; }
        toast.success(`Strategie "${d.name || out.id}" 1:1 wiederhergestellt – Panel neu öffnen, um die Werte zu sehen`);
      } catch { toast.error('Datei konnte nicht gelesen werden'); }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  return (
    <SafeOverlay className="settings-overlay" onClose={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()} data-testid="settings-panel">
        <div className="settings-header">
          <h2>EINSTELLUNGEN {saving && <span className="text-muted" style={{fontSize: '12px'}}>· Speichere...</span>}</h2>
          <button className="settings-close" onClick={onClose} data-testid="settings-close-button">
            <X size={24} weight="bold" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="settings-tabs">
          {mode !== 'general' && (
            <button 
              className={`settings-tab ${activeTab === 'strategy' ? 'active' : ''}`}
              onClick={() => setActiveTab('strategy')}
              data-testid="tab-strategy"
            >
              <ChartLineUp size={16} weight="bold" />
              Strategie
            </button>
          )}
          {mode !== 'general' && (
            <button 
              className={`settings-tab ${activeTab === 'sessions' ? 'active' : ''}`}
              onClick={() => setActiveTab('sessions')}
              data-testid="tab-sessions"
            >
              <Lightning size={16} weight="bold" />
              Zeitfenster
            </button>
          )}
          {/* NEW: Steuerung Tab - links neben Telegram */}
          {mode !== 'strategy' && (
            <button 
              className={`settings-tab ${activeTab === 'control' ? 'active' : ''}`}
              onClick={() => setActiveTab('control')}
              data-testid="tab-control"
            >
              <Power size={16} weight="bold" />
              Steuerung
            </button>
          )}
          {mode !== 'strategy' && (
            <button 
              className={`settings-tab ${activeTab === 'telegram' ? 'active' : ''}`}
              onClick={() => setActiveTab('telegram')}
              data-testid="tab-telegram"
            >
              <TelegramLogo size={16} weight="bold" />
              Telegram
            </button>
          )}
        </div>

        <div className="settings-content">
          {/* STRATEGY TAB */}
          {activeTab === 'strategy' && (
            <>
              {activeStrategy && (
                <div className="settings-section">
                  <div className="section-simple-header">
                    <h3>
                      <Sliders size={18} weight="bold" style={{marginRight: '8px', display: 'inline-block', verticalAlign: 'middle'}} />
                      {activeStrategy.name}
                    </h3>
                    <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span className="text-muted" style={{ fontSize: 12 }}>Gilt für:</span>
                      <select
                        value={paramCoin}
                        onChange={(e) => setParamCoin(e.target.value)}
                        data-testid="param-coin-select"
                        style={{ background: '#0A0A0A', border: '1px solid #2A2D3A', borderRadius: 8, padding: '7px 10px', color: '#fff' }}
                      >
                        <option value="">Alle Coins (Global)</option>
                        {ALL_COINS.map(c => <option key={c} value={c}>{c.replace('USDT', '')}</option>)}
                      </select>
                      {paramCoin && <span className="param-custom-badge">PRO COIN</span>}
                    </div>
                    <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span className="text-muted" style={{ fontSize: 12 }}>Timeframe (Signale, Paper &amp; Live):</span>
                      <select
                        value={settings.strategy_timeframes?.[activeStrategy.id] || activeStrategy.timeframe || '1m'}
                        onChange={(e) => updateStrategyTimeframe(activeStrategy.id, e.target.value)}
                        data-testid="strategy-timeframe-select"
                        style={{ background: '#0A0A0A', border: '1px solid #2A2D3A', borderRadius: 8, padding: '7px 10px', color: '#fff' }}
                      >
                        {TIMEFRAMES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
                      </select>
                      {settings.strategy_timeframes?.[activeStrategy.id] &&
                        settings.strategy_timeframes[activeStrategy.id] !== '1m' && (
                          <span className="param-custom-badge">CUSTOM</span>
                        )}
                    </div>
                  </div>

                  <div className="params-list">
                    {Object.entries(activeStrategy.params || {}).map(([paramKey, paramMeta]) => {
                      const currentValue = getCurrentParamValue(
                        activeStrategy.id, 
                        paramKey, 
                        paramMeta.value
                      );
                      const isCustom = paramCoin
                        ? settings.coin_params?.[activeStrategy.id]?.[paramCoin]?.[paramKey] !== undefined
                        : settings.strategy_params[activeStrategy.id]?.[paramKey] !== undefined;
                      
                      return (
                        <div key={paramKey} className="param-item" data-testid={`param-${paramKey}`}>
                          <div className="param-info">
                            <div className="param-label">
                              {paramMeta.label}
                              {isCustom && <span className="param-custom-badge">CUSTOM</span>}
                            </div>
                            <div className="param-description">
                              {paramMeta.description}
                            </div>
                            <div className="param-range">
                              Min: {paramMeta.min} · Max: {paramMeta.max} · Default: {paramMeta.value}
                            </div>
                          </div>
                          <div className="param-input-wrapper">
                            <input
                              type="number"
                              className="param-input"
                              value={currentValue}
                              min={paramMeta.min}
                              max={paramMeta.max}
                              step={paramMeta.step}
                              onChange={(e) => updateStrategyParam(activeStrategy.id, paramKey, e.target.value)}
                              onBlur={() => commitParams(activeStrategy.id)}
                              data-testid={`param-input-${paramKey}`}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {activeStrategy.is_custom && activeStrategy.definition && (
                    <div data-testid="custom-def-editor" style={{ marginTop: 16 }}>
                      <div className="param-label" style={{ color: '#B388FF', marginBottom: 8 }}>
                        REGELN &amp; INDIKATOR-PERIODEN (Custom/Discovery · dauerhaft)
                        {defDirty && <span className="param-custom-badge" style={{ marginLeft: 8 }}>NICHT GESPEICHERT</span>}
                      </div>
                      <div className="params-list">
                        {(activeStrategy.definition.long_rules || []).map((r, i) => (
                          <div key={`L${i}`} className="param-item" data-testid={`def-rule-long-${i}`}>
                            <div className="param-info">
                              <div className="param-label" style={{ color: '#30D158' }}>
                                LONG: {r.label || `${r.indicator} ${r.op}`}
                              </div>
                            </div>
                            <div className="param-input-wrapper">
                              {typeof r.value === 'number' ? (
                                <input type="number" step="any" className="param-input" value={r.value}
                                  onChange={e => updateDefRuleValue(activeStrategy.id, 'long_rules', i,
                                    e.target.value === '' ? 0 : parseFloat(e.target.value))}
                                  data-testid={`def-rule-long-input-${i}`} />
                              ) : (
                                <input type="text" className="param-input" value={String(r.value)} disabled
                                  title="Indikator-Vergleich – im Strategie-Builder änderbar" />
                              )}
                            </div>
                          </div>
                        ))}
                        {(activeStrategy.definition.short_rules || []).map((r, i) => (
                          <div key={`S${i}`} className="param-item" data-testid={`def-rule-short-${i}`}>
                            <div className="param-info">
                              <div className="param-label" style={{ color: '#FF6482' }}>
                                SHORT: {r.label || `${r.indicator} ${r.op}`}
                              </div>
                            </div>
                            <div className="param-input-wrapper">
                              {typeof r.value === 'number' ? (
                                <input type="number" step="any" className="param-input" value={r.value}
                                  onChange={e => updateDefRuleValue(activeStrategy.id, 'short_rules', i,
                                    e.target.value === '' ? 0 : parseFloat(e.target.value))}
                                  data-testid={`def-rule-short-input-${i}`} />
                              ) : (
                                <input type="text" className="param-input" value={String(r.value)} disabled
                                  title="Indikator-Vergleich – im Strategie-Builder änderbar" />
                              )}
                            </div>
                          </div>
                        ))}
                        {Object.entries(activeStrategy.definition.indicators || {}).map(([k, v]) => (
                          typeof v === 'number' ? (
                            <div key={k} className="param-item" data-testid={`def-ind-${k}`}>
                              <div className="param-info">
                                <div className="param-label">{k}</div>
                                <div className="param-description">Indikator-Periode/Einstellung</div>
                              </div>
                              <div className="param-input-wrapper">
                                <input type="number" step="any" className="param-input" value={v}
                                  onChange={e => updateDefIndicator(activeStrategy.id, k,
                                    e.target.value === '' ? 0 : parseFloat(e.target.value))}
                                  data-testid={`def-ind-input-${k}`} />
                              </div>
                            </div>
                          ) : null
                        ))}
                      </div>
                      <button className="btn" onClick={() => saveDefinition(activeStrategy.id)}
                        disabled={!defDirty} data-testid="save-definition-btn"
                        style={{ marginTop: 8 }}>
                        {defDirty ? '💾 Regeln speichern' : 'Regeln gespeichert'}
                      </button>
                    </div>
                  )}

                  <button 
                    className="btn btn-reset"
                    onClick={() => resetStrategyParams(activeStrategy.id)}
                    data-testid="reset-params-btn"
                  >
                    Alle Parameter zurücksetzen
                  </button>
                  <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                    <button className="btn" onClick={exportStrategyBackup} data-testid="strategy-export-btn"
                      title="Komplette Strategie sichern: Regeln, Parameter, Timeframe, Zeitfenster, Live/Paper-Einstellungen">
                      ⬇ Strategie komplett exportieren
                    </button>
                    <button className="btn" onClick={() => importParamsRef.current?.click()} data-testid="strategy-import-btn"
                      title="Backup-Datei laden – stellt alle Einstellungen 1:1 wieder her">
                      ⬆ Backup laden
                    </button>
                    <input ref={importParamsRef} type="file" accept=".json,application/json"
                      style={{ display: 'none' }} onChange={importStrategyBackup} data-testid="strategy-import-file" />
                  </div>
                </div>
              )}

              <div className="settings-section">
                <div className="setting-toggle">
                  <div className="toggle-info">
                    <div className="toggle-title">Pre-Signal Warnungen aktivieren</div>
                    <div className="toggle-description">
                      Frühwarnungen wenn Signal in Kürze zu erwarten ist
                    </div>
                  </div>
                  <label className="switch">
                    <input 
                      type="checkbox" 
                      checked={settings.pre_signal_enabled !== false}
                      onChange={(e) => togglePreSignal(e.target.checked)}
                      data-testid="pre-signal-toggle"
                    />
                    <span className="slider"></span>
                  </label>
                </div>
              </div>
            </>
          )}

          {/* SESSIONS TAB */}
          {activeTab === 'sessions' && (
            <>
              <div className="settings-section">
                <div className="session-scope-row" style={{ marginBottom: 12 }}>
                  <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>
                    Zeitfenster gelten für:
                  </label>
                  <select
                    value={sessionScope}
                    onChange={(e) => setSessionScope(e.target.value)}
                    data-testid="session-scope-select"
                    style={{ width: '100%', padding: '8px', background: 'rgba(0,0,0,0.3)', color: 'inherit', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6 }}
                  >
                    <option value="global">🌍 Global (alle Strategien ohne eigenes Fenster)</option>
                    {strategies.map(s => (
                      <option key={s.id} value={s.id}>
                        {s.name}{(settings.strategy_sessions?.[s.id]?.length) ? ' · eigenes Zeitfenster ✓' : ''}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="session-mode-info">
                  {is24_7 ? (
                    <div className="mode-badge mode-badge-active">
                      {isGlobalScope ? '⚡ 24/7 MODUS AKTIV' : '⚡ Folgt dem GLOBALEN Zeitfenster'}
                    </div>
                  ) : (
                    <div className="mode-badge">
                      📅 {scopedSessions.filter(s => s.enabled).length} Zeitfenster
                      {!isGlobalScope && ' (nur diese Strategie)'}
                    </div>
                  )}
                </div>

                <div className="sessions-list">
                  {scopedSessions.map((session, index) => (
                    <div key={index} className="session-item" data-testid={`session-${index}`}>
                      <label className="switch switch-small">
                        <input 
                          type="checkbox" 
                          checked={session.enabled !== false}
                          onChange={() => toggleSession(index)}
                        />
                        <span className="slider"></span>
                      </label>
                      <input 
                        type="text" className="session-name"
                        value={session.name || ''}
                        onChange={(e) => updateSession(index, 'name', e.target.value)}
                        onBlur={commitSessionUpdate}
                      />
                      <div className="session-times">
                        <input 
                          type="time" value={session.start || '09:00'}
                          onChange={(e) => updateSession(index, 'start', e.target.value)}
                          onBlur={commitSessionUpdate}
                          className="time-input"
                        />
                        <span className="text-muted">-</span>
                        <input 
                          type="time" value={session.end || '12:00'}
                          onChange={(e) => updateSession(index, 'end', e.target.value)}
                          onBlur={commitSessionUpdate}
                          className="time-input"
                        />
                      </div>
                      <button 
                        className="btn-icon-remove"
                        onClick={() => removeSession(index)}
                      >
                        <Trash size={16} />
                      </button>
                    </div>
                  ))}
                </div>

                <div className="session-actions">
                  <button className="btn btn-add-session" onClick={addSession} data-testid="add-session-btn">
                    <Plus size={16} weight="bold" />
                    Zeitfenster hinzufügen
                  </button>
                  {!is24_7 && (
                    <button className="btn btn-24-7" onClick={enable24_7} data-testid="enable-24-7-btn">
                      <Lightning size={16} weight="bold" />
                      {isGlobalScope ? '24/7 Modus' : 'Eigenes Fenster löschen (global nutzen)'}
                    </button>
                  )}
                  {is24_7 && (
                    <button className="btn" onClick={restoreDefaults} data-testid="restore-defaults-btn">
                      Standard (London + US)
                    </button>
                  )}
                </div>
                
                <div className="info-hint">
                  💡 Alle Zeiten in deutscher Zeit (MEZ/CET). Ohne Zeitfenster → 24/7 Modus.
                  Strategien mit eigenem Zeitfenster ignorieren das globale Fenster.
                  Im Backtester lassen sich Zeitfenster pro Strategie ebenfalls testen (⚙-Panel).
                </div>
              </div>
            </>
          )}

          {/* NEW: STEUERUNG TAB */}
          {activeTab === 'control' && (
            <>
              <div className="settings-section">
                <div className="section-simple-header">
                  <h3>
                    <Power size={18} weight="bold" style={{marginRight: '8px', display: 'inline-block', verticalAlign: 'middle'}} />
                    Master-Steuerung
                  </h3>
                </div>

                <div className="control-card" data-testid="control-trades-card">
                  <div className="control-card-header">
                    <div className="control-card-info">
                      <div className="control-card-title">Bot-Trades</div>
                      <div className="control-card-desc">
                        Alle automatischen Trades global starten oder stoppen. Beim Stoppen werden offene Bot-Trades geschlossen.
                      </div>
                    </div>
                    <button
                      className={`control-master-btn ${controlState?.trades_paused ? 'paused' : 'active'}`}
                      onClick={() => toggleControl('trades')}
                      disabled={busy}
                      data-testid="control-toggle-trades"
                    >
                      {controlState?.trades_paused
                        ? <PlayCircle size={22} weight="fill" />
                        : <PauseCircle size={22} weight="fill" />}
                      <span className="control-master-label">
                        {controlState?.trades_paused ? 'TRADES AUS' : 'TRADES AN'}
                      </span>
                      <span className={`control-master-pill ${controlState?.trades_paused ? 'off' : 'on'}`}>
                        {controlState?.trades_paused ? 'GESTOPPT' : 'AKTIV'}
                      </span>
                    </button>
                  </div>
                </div>

                <div className="control-card" data-testid="control-signals-card">
                  <div className="control-card-header">
                    <div className="control-card-info">
                      <div className="control-card-title">Signale</div>
                      <div className="control-card-desc">
                        Alle Signal-Benachrichtigungen global aktivieren oder deaktivieren.
                      </div>
                    </div>
                    <button
                      className={`control-master-btn ${controlState?.signals_paused ? 'paused' : 'active'}`}
                      onClick={() => toggleControl('signals')}
                      disabled={busy}
                      data-testid="control-toggle-signals"
                    >
                      {controlState?.signals_paused
                        ? <PlayCircle size={22} weight="fill" />
                        : <PauseCircle size={22} weight="fill" />}
                      <span className="control-master-label">
                        {controlState?.signals_paused ? 'SIGNALE AUS' : 'SIGNALE AN'}
                      </span>
                      <span className={`control-master-pill ${controlState?.signals_paused ? 'off' : 'on'}`}>
                        {controlState?.signals_paused ? 'GESTOPPT' : 'AKTIV'}
                      </span>
                    </button>
                  </div>
                </div>

                <div className="info-hint">
                  💡 Diese Einstellungen gelten global für alle Strategien und Coins. Änderungen werden sofort wirksam.
                </div>
              </div>
            </>
          )}

          {/* TELEGRAM TAB */}
          {activeTab === 'telegram' && (
            <>
              <div className="settings-section">
                <div className="info-box" style={{ borderColor: '#00FF66' }}>
                  <div className="info-text">
                    ✅ <strong>Bot verbunden:</strong> @Krypto_Strategy_Alert_Bot
                    <br />
                    Signale werden automatisch an dich gesendet
                  </div>
                </div>

                <button 
                  className="btn btn-long" 
                  onClick={handleTestTelegram} 
                  disabled={testing}
                  data-testid="test-telegram-button"
                >
                  {testing ? 'Teste...' : 'Test-Nachricht senden'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </SafeOverlay>
  );
};

export default SettingsPanel;
