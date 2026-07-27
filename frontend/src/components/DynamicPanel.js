import React, { useState, useEffect, useCallback } from 'react';
import { ArrowsClockwise, CheckCircle, Trash, CaretDown, CaretRight } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

export default function DynamicPanel() {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState(null);
  const [busy, setBusy] = useState({});
  const [refreshDays, setRefreshDays] = useState(30);

  const load = useCallback(() => {
    fetch(`${API_URL}/api/dynamic/list`).then(r => r.json())
      .then(d => setList(d.strategies || [])).catch(() => setList([]));
  }, []);

  useEffect(() => { if (open) load(); }, [open, load]);

  const refresh = async (id) => {
    setBusy(b => ({ ...b, [id]: 'refresh' }));
    try {
      const r = await fetch(`${API_URL}/api/dynamic/${id}/refresh`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days: refreshDays }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Aktualisierung fehlgeschlagen'); return; }
      toast.success('Regime aktualisiert');
      load();
    } catch { toast.error('Verbindungsfehler'); }
    finally { setBusy(b => ({ ...b, [id]: null })); }
  };

  const apply = async (id) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setBusy(b => ({ ...b, [id]: 'apply' }));
    try {
      const r = await fetch(`${API_URL}/api/dynamic/${id}/apply`, {
        method: 'POST', headers: authHeaders(),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Übernahme fehlgeschlagen'); return; }
      toast.success(`Aktive Regime-Konfiguration übernommen (${(d.applied || []).length} Coins)`);
      load();
    } catch { toast.error('Verbindungsfehler'); }
    finally { setBusy(b => ({ ...b, [id]: null })); }
  };

  const remove = async (id) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try {
      const r = await fetch(`${API_URL}/api/dynamic/${id}`, { method: 'DELETE', headers: authHeaders() });
      if (!r.ok) { toast.error('Löschen fehlgeschlagen'); return; }
      toast.success('Dynamische Strategie gelöscht');
      load();
    } catch { toast.error('Verbindungsfehler'); }
  };

  return (
    <div className="dyn-panel" data-testid="dyn-panel">
      <button className={`opt-chip opt-history-btn ${open ? 'on' : ''}`}
        onClick={() => setOpen(!open)} data-testid="dyn-panel-toggle"
        title="Gespeicherte dynamische Strategien: aktuelles Regime prüfen und die passende Konfiguration übernehmen">
        {open ? <CaretDown size={13} /> : <CaretRight size={13} />} Dynamische Strategien
      </button>
      {open && (
        <div className="opt-history" data-testid="dyn-panel-body">
          <div className="opt-small" style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            Analyse-Zeitraum für die Regime-Bestimmung:
            <select value={refreshDays} onChange={e => setRefreshDays(parseInt(e.target.value))} data-testid="dyn-refresh-days">
              {[10, 20, 30, 60, 90].map(d => <option key={d} value={d}>{d} Tage</option>)}
            </select>
          </div>
          {list === null && <div className="opt-small">Lade...</div>}
          {list !== null && list.length === 0 && (
            <div className="opt-small">Noch keine dynamischen Strategien – Modus "Dynamische Strategie" starten und Ergebnis speichern.</div>
          )}
          {(list || []).map(s => {
            const per = (s.last_state || {}).per_symbol || {};
            return (
              <div key={s.id} className="dyn-card" data-testid={`dyn-card-${s.id}`}>
                <div className="dyn-card-head">
                  <b>{s.name}</b>
                  <span className="opt-small">Basis: {s.strategy_id} · {s.timeframe} · {(s.symbols || []).map(x => x.replace('USDT', '')).join(', ')}</span>
                  <span className="opt-small">{(s.model?.regimes || []).length} Regime</span>
                  {s.verdict?.dynamic_better === false && <span className="opt-badge bad" title="Beim Erstellen war die statische Benchmark besser">Benchmark war besser</span>}
                  <span style={{ flex: 1 }} />
                  <button className="opt-chip" onClick={() => refresh(s.id)} disabled={!!busy[s.id]} data-testid={`dyn-refresh-${s.id}`}>
                    <ArrowsClockwise size={12} /> {busy[s.id] === 'refresh' ? 'Prüfe...' : 'Regime aktualisieren'}
                  </button>
                  <button className="opt-chip" onClick={() => apply(s.id)} disabled={!!busy[s.id] || !Object.keys(per).length} data-testid={`dyn-apply-${s.id}`}
                    title="Aktive Regime-Konfiguration je Coin als Live/Paper-Override übernehmen">
                    <CheckCircle size={12} /> Konfiguration übernehmen
                  </button>
                  <button className="opt-chip" onClick={() => remove(s.id)} data-testid={`dyn-delete-${s.id}`}>
                    <Trash size={12} />
                  </button>
                </div>
                {s.last_applied && <div className="opt-small">Zuletzt übernommen: {new Date(s.last_applied).toLocaleString('de-DE')}</div>}
                {Object.entries(per).map(([sym, st]) => st.error ? (
                  <div key={sym} className="opt-small neg">{sym}: {st.error}</div>
                ) : (
                  <div key={sym} className="dyn-regime-state" data-testid={`dyn-state-${s.id}-${sym}`}>
                    <div>
                      <b>{sym.replace('USDT', '')}</b> · Aktuelles Regime: <b>{st.label || '–'}</b> ·
                      Sicherheit: <b className={st.confidence >= 70 ? 'pos' : ''}>{fmt(st.confidence, 0)}%</b>
                      {st.confidence < 70 && <span className="opt-small" style={{ color: '#FFB74D' }}> (unter Schwelle – aktuelle Konfiguration behalten)</span>}
                      {st.last_switch && <span className="opt-small"> · letzter Wechsel: {new Date(st.last_switch).toLocaleDateString('de-DE')}</span>}
                    </div>
                    <div className="opt-small">
                      Ähnlichkeiten: {(st.similarities || []).map(x => `${x.label} ${fmt(x.similarity_pct, 0)}%`).join(' · ')}
                    </div>
                    <div className="opt-params-list" style={{ margin: '3px 0' }}>
                      <span className="opt-small" style={{ alignSelf: 'center' }}>Aktive Konfig:</span>
                      {Object.entries(st.active_config || {}).length
                        ? Object.entries(st.active_config).map(([k, v]) => <span key={k} className="opt-param-pill">{k}: <b>{String(v)}</b></span>)
                        : <span className="opt-param-pill">Baseline (aktuelle Einstellungen)</span>}
                    </div>
                    {(st.recent_performance || []).length > 0 && (
                      <div className="opt-small" title="Nur zur Info – die Umschaltung basiert auf der Regime-Ähnlichkeit, NICHT auf der jüngsten Performance (Overfitting-Schutz)">
                        Vergleich letzte {(s.last_state || {}).days} Tage: {st.recent_performance.map(p =>
                          `${p.label}: ${fmt(p.pnl, 1)} (${p.trades} T.)`).join(' · ')}
                      </div>
                    )}
                  </div>
                ))}
                {!Object.keys(per).length && <div className="opt-small">Noch nicht geprüft – "Regime aktualisieren" klicken.</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
