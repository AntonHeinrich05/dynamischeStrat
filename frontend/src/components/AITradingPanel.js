import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Robot, PaperPlaneRight, X, Trash, ArrowsClockwise, Lightning, CaretDown, CaretUp, Newspaper, PushPin, Brain, GraduationCap, CheckCircle, XCircle, Sliders, Coins } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders } from '../auth';
import './AITradingPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const MODEL_OPTIONS = [
  // Google Gemini (GEMINI_API_KEY)
  { provider: 'gemini', model: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash (Standard, schnell & aktuell)' },
  { provider: 'gemini', model: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro (beste Qualität)' },
  { provider: 'gemini', model: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash-Lite (günstig)' },
  // Groq (GROQ_API_KEY) – extrem schnelle Inferenz, großzügiger Free-Tier
  { provider: 'groq', model: 'llama-3.3-70b-versatile', label: 'Groq · Llama 3.3 70B (kostenlos, sehr stark)' },
  { provider: 'groq', model: 'llama-3.1-8b-instant', label: 'Groq · Llama 3.1 8B Instant (kostenlos, blitzschnell)' },
  { provider: 'groq', model: 'qwen/qwen3-32b', label: 'Groq · Qwen3 32B (kostenlos)' },
  // OpenRouter (OPENROUTER_API_KEY) – Free-Katalog rotiert (Stand Juni 2026)
  { provider: 'openrouter', model: 'nvidia/nemotron-3-super-120b-a12b:free', label: 'OpenRouter · Nemotron-3 Super 120B (kostenlos, stark)' },
  { provider: 'openrouter', model: 'nvidia/nemotron-3-ultra-550b-a55b:free', label: 'OpenRouter · Nemotron-3 Ultra 550B (kostenlos, beste Qualität)' },
  { provider: 'openrouter', model: 'google/gemma-4-31b-it:free', label: 'OpenRouter · Gemma 4 31B (kostenlos)' },
  { provider: 'openrouter', model: 'openai/gpt-oss-20b:free', label: 'OpenRouter · GPT-OSS 20B (kostenlos)' },
  // Mistral (MISTRAL_API_KEY)
  { provider: 'mistral', model: 'mistral-small-latest', label: 'Mistral Small (kostenloses Free-Tier)' },
  { provider: 'mistral', model: 'open-mistral-nemo', label: 'Mistral Nemo 12B (kostenlos)' },
];

const actionClass = (a) => (a === 'LONG' ? 'ai-long' : a === 'SHORT' ? 'ai-short' : 'ai-hold');

const AUTONOMY_OPTIONS = [
  { value: 'off', label: 'Aus – KI ändert nichts' },
  { value: 'suggest', label: 'Vorschlagen – du bestätigst' },
  { value: 'auto', label: 'Automatisch – KI passt selbst an' },
];

const QUICK_PROMPTS = [
  'Wie ist deine aktuelle Performance?',
  'Was hast du zuletzt gelernt?',
  'Sei heute defensiv',
  'Begründe deine letzte Entscheidung',
];

// Alle handelbaren Assets (deckungsgleich mit backend core/config.py ALL_SYMBOLS)
const ALL_COINS = [
  'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'POLUSDT',
  'GOLD', 'SILVER', 'OIL',
];
const coinLabel = (s) => {
  const t = typeof s === 'string' ? s : String(s ?? '?');
  return ['GOLD', 'SILVER', 'OIL'].includes(t) ? t : t.replace('USDT', '');
};
const COIN_STORE_KEY = (coin) => `krypto_ai_chat_coins::${coin || 'BTCUSDT'}`;
const CHAT_FOCUS_STORE_KEY = 'krypto_ai_chat_focus_open';

const AITradingPanel = ({ onClose, selectedCoin = 'BTCUSDT' }) => {
  const [status, setStatus] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  // Coin-Auswahl für den Chat-Kontext (Feature: Coin-spezifischer KI-Chat)
  const [chatCoins, setChatCoins] = useState([selectedCoin]);
  const [proposals, setProposals] = useState([]);
  const [insights, setInsights] = useState(null);
  const [showLearn, setShowLearn] = useState(false);
  const [learning, setLearning] = useState(false);
  // "Coin-Fokus"-Bereich ein-/ausklappbar (Standard: eingeklappt, persistiert in localStorage)
  const [showChatFocus, setShowChatFocus] = useState(() => {
    try { return localStorage.getItem(CHAT_FOCUS_STORE_KEY) === '1'; } catch (e) { return false; }
  });

  // Entweder-Oder: beim Öffnen eines Panels werden die anderen geschlossen.
  const closeChatFocus = () => {
    setShowChatFocus(false);
    try { localStorage.setItem(CHAT_FOCUS_STORE_KEY, '0'); } catch (e) { /* ignore */ }
  };
  const toggleChatFocus = () => {
    setShowChatFocus(prev => {
      const next = !prev;
      try { localStorage.setItem(CHAT_FOCUS_STORE_KEY, next ? '1' : '0'); } catch (e) { /* ignore */ }
      if (next) { setShowLearn(false); setShowSetup(false); }
      return next;
    });
  };
  const toggleLearn = () => {
    setShowLearn(prev => {
      const next = !prev;
      if (next) { loadInsights(); setShowSetup(false); closeChatFocus(); }
      return next;
    });
  };
  const toggleSetup = () => {
    setShowSetup(prev => {
      const next = !prev;
      if (next) { setShowLearn(false); closeChatFocus(); }
      return next;
    });
  };
  const chatEndRef = useRef(null);
  const chatAreaRef = useRef(null);
  const atBottomRef = useRef(true);
  const streamingRef = useRef(false);
  const stripRef = useRef(null);
  const stripDrag = useRef({ active: false, startX: 0, scrollLeft: 0, moved: false });

  const onStripMouseDown = (e) => {
    const el = stripRef.current;
    if (!el) return;
    stripDrag.current = { active: true, startX: e.pageX, scrollLeft: el.scrollLeft, moved: false };
  };
  const onStripMouseMove = (e) => {
    const el = stripRef.current;
    if (!el || !stripDrag.current.active) return;
    const dx = e.pageX - stripDrag.current.startX;
    if (Math.abs(dx) > 4) stripDrag.current.moved = true;
    el.scrollLeft = stripDrag.current.scrollLeft - dx;
  };
  const endStripDrag = () => { stripDrag.current.active = false; };

  // Chip-Reihenfolge: aktueller Coin immer vorne, danach der Rest.
  const orderedCoins = React.useMemo(
    () => [selectedCoin, ...ALL_COINS.filter(c => c !== selectedCoin)],
    [selectedCoin],
  );
  const allSelected = chatCoins.length >= ALL_COINS.length;
  // Kompakte Anzeige der aktuellen Auswahl (für Button-Text & Tooltip)
  const focusSummary = allSelected ? 'alle' : chatCoins.map(coinLabel).join(', ');

  // Beim Öffnen / Coin-Wechsel: gespeicherte Auswahl je Coin-Ansicht laden,
  // sonst standardmäßig nur den aktuell geöffneten Coin vorwählen.
  useEffect(() => {
    let next = [selectedCoin];
    try {
      const raw = localStorage.getItem(COIN_STORE_KEY(selectedCoin));
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length) {
          next = parsed.filter(c => ALL_COINS.includes(c));
          if (!next.length) next = [selectedCoin];
        }
      }
    } catch (e) { /* ignore */ }
    setChatCoins(next);
  }, [selectedCoin]);

  const persistCoins = (coins) => {
    setChatCoins(coins);
    try { localStorage.setItem(COIN_STORE_KEY(selectedCoin), JSON.stringify(coins)); } catch (e) { /* ignore */ }
  };

  const toggleCoin = (coin) => {
    const has = chatCoins.includes(coin);
    let next = has ? chatCoins.filter(c => c !== coin) : [...chatCoins, coin];
    if (!next.length) next = [selectedCoin]; // mind. ein Coin bleibt aktiv
    persistCoins(next);
  };

  const toggleAll = () => {
    persistCoins(allSelected ? [selectedCoin] : [...ALL_COINS]);
  };

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/ai/status`).then(r => r.json());
      setStatus(data && typeof data === 'object' ? data : null);
    } catch (e) { /* silent */ }
  }, []);

  const loadHistory = useCallback(async () => {
    if (streamingRef.current) return;
    try {
      const data = await fetch(`${API_URL}/api/ai/chat/history?limit=100`).then(r => r.json());
      setMessages(data.messages || []);
    } catch (e) { /* silent */ }
  }, []);

  const loadProposals = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/ai/proposals?status=pending&limit=20`).then(r => r.json());
      setProposals(data.proposals || []);
    } catch (e) { /* silent */ }
  }, []);

  const loadInsights = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/ai/insights`).then(r => r.json());
      setInsights(data && typeof data === 'object' ? data : null);
    } catch (e) { /* silent */ }
  }, []);

  useEffect(() => {
    loadStatus(); loadHistory(); loadProposals(); loadInsights();
    const iv = setInterval(() => { loadStatus(); loadHistory(); loadProposals(); }, 12000);
    return () => clearInterval(iv);
  }, [loadStatus, loadHistory, loadProposals, loadInsights]);

  // Nur automatisch ans Ende scrollen, wenn der Nutzer ohnehin (fast) unten ist.
  // Scrollt der Nutzer nach oben, um zu lesen, bleibt die Position erhalten –
  // auch wenn das 12s-Polling neue Nachrichten nachlädt.
  const onChatScroll = () => {
    const el = chatAreaRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  useEffect(() => {
    if (atBottomRef.current) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, streamText]);

  const cfg = status?.config || {};
  const decisions = status?.decisions || {};
  // Entscheidung (LONG/SHORT/HOLD) zu einem Coin finden – egal ob per Key oder Symbol abgelegt
  const decisionFor = (coin) => decisions[coin] || Object.values(decisions).find(d => d?.symbol === coin) || null;

  const updateConfig = async (updates) => {
    try {
      const res = await fetch(`${API_URL}/api/ai/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(updates),
      });
      if (!res.ok) { toast.error('Nicht autorisiert'); return; }
      const data = await res.json();
      setStatus(prev => ({ ...prev, config: data.config }));
      if ('enabled' in updates) toast.success(`KI Trader ${updates.enabled ? 'AKTIVIERT' : 'gestoppt'}`);
    } catch (e) { toast.error('Verbindungsfehler'); }
  };

  const analyzeNow = async () => {
    setAnalyzing(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/analyze`, { method: 'POST', headers: authHeaders() });
      const data = await res.json();
      if (data.status === 'ok') {
        toast.success(`Analyse fertig: ${data.decisions} Coins, ${(data.signals || []).length} Signal(e)`);
      } else {
        toast.error(data.detail || 'Analyse fehlgeschlagen');
      }
      loadStatus(); loadHistory();
    } catch (e) { toast.error('Verbindungsfehler'); }
    setAnalyzing(false);
  };

  const clearChat = async () => {
    await fetch(`${API_URL}/api/ai/chat`, { method: 'DELETE', headers: authHeaders() });
    setMessages([]);
    toast.success('Chat geleert');
  };

  const learnNow = async () => {
    setLearning(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/learn`, { method: 'POST', headers: authHeaders() });
      const data = await res.json();
      if (data.status === 'ok') {
        toast.success(`Lernlauf fertig: ${data.lessons} Lektionen${data.config_changes ? `, ${data.config_changes} Einstellungs-Änderung(en)` : ''}`);
      } else {
        toast.error(data.detail || 'Lernlauf fehlgeschlagen');
      }
      loadInsights(); loadHistory(); loadProposals(); loadStatus();
    } catch (e) { toast.error('Verbindungsfehler'); }
    setLearning(false);
  };

  const decideProposal = async (pid, action) => {
    try {
      const res = await fetch(`${API_URL}/api/ai/proposals/${pid}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) { toast.error(res.status === 401 ? 'Admin-Login erforderlich' : 'Fehler'); return; }
      toast.success(action === 'approve' ? 'Änderung übernommen' : 'Vorschlag abgelehnt');
      loadProposals(); loadHistory();
    } catch (e) { toast.error('Verbindungsfehler'); }
  };

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput('');
    setMessages(prev => [...prev, { id: `local-${Date.now()}`, role: 'user', text, ts: new Date().toISOString() }]);
    setStreaming(true);
    streamingRef.current = true;
    setStreamText('');
    let acc = '';
    try {
      const res = await fetch(`${API_URL}/api/ai/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ message: text, coins: allSelected ? ['ALL'] : chatCoins }),
      });
      if (!res.ok) {
        toast.error(res.status === 401 ? 'Admin-Login erforderlich' : 'Chat-Fehler');
      } else {
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const line = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 2);
            if (!line.startsWith('data: ')) continue;
            try {
              const p = JSON.parse(line.slice(6));
              if (p.t) { acc += p.t; setStreamText(acc); }
              if (p.error) toast.error(p.error);
            } catch (e) { /* skip */ }
          }
        }
      }
    } catch (e) { toast.error('Verbindungsfehler'); }
    if (acc) {
      setMessages(prev => [...prev, { id: `local-a-${Date.now()}`, role: 'assistant', text: acc, ts: new Date().toISOString() }]);
    }
    setStreamText('');
    setStreaming(false);
    streamingRef.current = false;
  };

  const fmtTime = (ts) => {
    try { return new Date(ts).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' }); }
    catch { return ''; }
  };

  const renderMessage = (m) => {
    if (m.role === 'learning') {
      return (
        <div key={m.id} className="ai-msg ai-msg-learning" data-testid="ai-learning-message">
          <div className="ai-analysis-head">
            <GraduationCap size={14} weight="fill" />
            <span>LERN-UPDATE {m.trigger === 'trade_close' ? '(nach Trade)' : m.trigger === 'daily' ? '(täglich)' : '(manuell)'}</span>
            <span className="ai-msg-time">{fmtTime(m.ts)}</span>
          </div>
          {m.text && <div className="ai-analysis-overview">{m.text}</div>}
          {(m.lessons || []).length > 0 && (
            <ol className="ai-lesson-list">
              {m.lessons.map((l, i) => <li key={i}><b>{l.title}</b>: {l.detail}</li>)}
            </ol>
          )}
        </div>
      );
    }
    if (m.role === 'config') {
      return (
        <div key={m.id} className="ai-msg ai-msg-config" data-testid="ai-config-message">
          <div className="ai-analysis-head">
            <Sliders size={14} weight="bold" />
            <span>EINSTELLUNGS-{(m.items || []).some(i => i.status === 'pending') ? 'VORSCHLAG' : 'ÄNDERUNG'}</span>
            <span className="ai-msg-time">{fmtTime(m.ts)}</span>
          </div>
          {m.text && <div className="ai-analysis-overview">{m.text}</div>}
          {(m.items || []).map((it, i) => (
            <div key={i} className={`ai-config-item ai-config-${it.status}`}>
              <b>{it.symbol === 'ENGINE' ? 'Engine' : coinLabel(it.symbol)}</b>{' '}
              {Object.entries(it.changes || {}).map(([k, v]) => (
                <span key={k} className="ai-prop-chg">{k}: <s>{String(it.current?.[k])}</s> → <b>{String(v)}</b></span>
              ))}
              <span className={`ai-config-status ai-config-status-${it.status}`}>
                {it.status === 'auto_applied' ? 'automatisch übernommen'
                  : it.status === 'applied' ? 'übernommen'
                    : it.status === 'rejected' ? 'abgelehnt' : 'wartet auf Bestätigung'}
              </span>
              {it.reason && <div className="ai-prop-reason">{it.reason}</div>}
            </div>
          ))}
        </div>
      );
    }
    if (m.role === 'summary') {
      const cfg = m.active_config || {};
      const counts = m.counts || {};
      const directives = Array.isArray(m.directives) ? m.directives : [];
      return (
        <div
          key={m.id}
          className={`ai-msg ai-msg-summary${m.pinned ? ' ai-msg-summary-pinned' : ''}`}
          data-testid="ai-summary-message"
        >
          <div className="ai-summary-head">
            <PushPin size={13} weight="fill" />
            <span className="ai-summary-badge" data-testid="ai-summary-badge">
              Tages-Zusammenfassung{m.day ? ` · ${m.day}` : ''}
            </span>
            {m.fallback && (
              <span className="ai-summary-fallback" title="LLM war nicht erreichbar – rein statistische Zusammenfassung">
                statistisch
              </span>
            )}
            <span className="ai-msg-time">{fmtTime(m.ts)}</span>
          </div>
          {m.text && <div className="ai-summary-text">{m.text}</div>}
          {(counts && Object.keys(counts).length > 0) && (
            <div className="ai-summary-metrics" data-testid="ai-summary-metrics">
              <span><b>{counts.analyses ?? 0}</b> Analysen</span>
              <span><b>{counts.signals ?? 0}</b> Signale</span>
              <span><b>{counts.long ?? 0}</b> LONG</span>
              <span><b>{counts.short ?? 0}</b> SHORT</span>
              <span><b>{counts.hold ?? 0}</b> HOLD</span>
            </div>
          )}
          {directives.length > 0 && (
            <div className="ai-summary-directives">
              <div className="ai-summary-sub">Deine Trader-Direktiven (aktuell aktiv):</div>
              <ul>
                {directives.slice(-6).map((d, i) => (<li key={i}>{d}</li>))}
              </ul>
            </div>
          )}
          {(cfg.provider || cfg.model) && (
            <div className="ai-summary-config" title="Aktive Konfiguration wonach die KI gerade tradet">
              KI tradet nach: <b>{cfg.provider}/{cfg.model}</b> · Intervall <b>{cfg.interval_min} min</b> · Min. Konfidenz <b>{cfg.min_confidence}%</b> · Cooldown <b>{cfg.cooldown_min} min</b> · News <b>{cfg.news_enabled ? 'an' : 'aus'}</b>
            </div>
          )}
        </div>
      );
    }
    if (m.role === 'analysis') {
      return (
        <div key={m.id} className="ai-msg ai-msg-analysis" data-testid="ai-analysis-message">
          <div className="ai-analysis-head">
            <Robot size={14} weight="fill" />
            <span>MARKT-ANALYSE {m.manual ? '(manuell)' : ''}</span>
            <span className="ai-msg-time">{fmtTime(m.ts)}</span>
          </div>
          {m.text && <div className="ai-analysis-overview">{m.text}</div>}
          {(m.decisions || []).length > 0 && (
            <div className="ai-analysis-decisions">
              {(m.decisions || []).map((d, i) => (
                <div key={i} className={`ai-decision-row ${actionClass(d?.action)}`}>
                  <span className="ai-dec-sym">{coinLabel(d?.symbol)}</span>
                  <span className={`ai-dec-action ${actionClass(d?.action)}`}>{d?.action || '–'}</span>
                  <span className="ai-dec-conf">{d?.confidence ?? 0}%</span>
                  {d?.signaled && <span className="ai-dec-signaled" title="Signal ausgelöst"><Lightning size={11} weight="fill" /></span>}
                  <span className="ai-dec-reason">{d?.reasoning || ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      );
    }
    const isUser = m.role === 'user';
    return (
      <div key={m.id} className={`ai-msg ${isUser ? 'ai-msg-user' : 'ai-msg-assistant'}`}
        data-testid={isUser ? 'ai-chat-user-message' : 'ai-chat-assistant-message'}>
        <div className="ai-msg-bubble">{m.text}</div>
        <div className="ai-msg-time">{fmtTime(m.ts)}</div>
      </div>
    );
  };

  const modelValue = (cfg.provider && cfg.model)
    ? `${cfg.provider}|${cfg.model}`
    : `${MODEL_OPTIONS[0].provider}|${MODEL_OPTIONS[0].model}`;

  return (
    <div className="ai-panel-overlay" onClick={onClose} data-testid="ai-trading-panel">
      <div className="ai-panel" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="ai-panel-header">
          <div className="ai-panel-title">
            <div className={`ai-robot-badge ${cfg.enabled ? 'on' : ''}`}><Robot size={20} weight="fill" /></div>
            <div>
              <h2>KI TRADER</h2>
              <span className="ai-panel-sub">
                {cfg.enabled
                  ? `Aktiv · analysiert alle ${cfg.interval_min} min${status?.analyzing ? ' · analysiert gerade…' : ''}`
                  : 'Ausgeschaltet – aktiviere die KI, damit sie eigenständig analysiert & tradet'}
              </span>
            </div>
          </div>
          <div className="ai-panel-header-actions">
            <button
              className={`ai-toggle ${cfg.enabled ? 'on' : ''}`}
              onClick={() => updateConfig({ enabled: !cfg.enabled })}
              data-testid="ai-enable-toggle"
            >
              <span className="ai-toggle-knob" />
              <span className="ai-toggle-label">{cfg.enabled ? 'AN' : 'AUS'}</span>
            </button>
            <button className="ai-icon-btn" onClick={onClose} data-testid="ai-panel-close"><X size={18} /></button>
          </div>
        </div>

        {!status?.has_key && (
          <div className="ai-warning" data-testid="ai-key-warning">
            ⚠ Für den Provider „{cfg.provider || 'gemini'}“ ist kein API-Key gesetzt (Render EnvVars:
            GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY / MISTRAL_API_KEY).
            Wähle im Setup ein Modell eines Providers, für den ein Key existiert.
          </div>
        )}
        {status?.last_error && (
          <div className="ai-warning" data-testid="ai-error-banner">
            ⚠ {status.last_error}
            {/FAILED_PRECONDITION|User location is not supported|location is not supported/i.test(status.last_error) && (
              <div style={{ marginTop: 6, fontSize: 12, opacity: 0.85 }}>
                Google blockiert deinen Server-Standort für den Gemini Free-Tier. Lösungen:
                <br />• Billing in <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">AI Studio</a> aktivieren (Free-Tier-Preise bleiben) – hebt Regional-Sperre auf
                <br />• Render-Region auf US-West/Oregon umstellen
                <br />• Vertex AI (EU-Endpoint) statt AI Studio verwenden
              </div>
            )}
          </div>
        )}

        {/* Status row */}
        <div className="ai-status-row">
          <button className="ai-action-btn" onClick={analyzeNow} disabled={analyzing || status?.analyzing} data-testid="ai-analyze-now-btn">
            <ArrowsClockwise size={14} weight="bold" className={analyzing || status?.analyzing ? 'spin' : ''} />
            {analyzing || status?.analyzing ? 'Analysiert…' : 'Jetzt analysieren'}
          </button>
          <span className="ai-status-info">
            Letzte Analyse: <b>{status?.last_run ? fmtTime(status.last_run) : '—'}</b>
          </span>
          <button
            className={`ai-setup-toggle ${showLearn ? 'active' : ''}`}
            onClick={toggleLearn}
            data-testid="ai-learn-toggle"
          >
            <Brain size={12} weight="bold" /> Lernen{status?.learning?.lessons_count ? ` (${status.learning.lessons_count})` : ''}
          </button>
          <button
            className={`ai-setup-toggle ai-focus-toggle ${showChatFocus ? 'active' : ''}`}
            onClick={toggleChatFocus}
            title={`KI-Chat Fokus: ${allSelected ? 'alle Coins' : chatCoins.map(coinLabel).join(', ')}`}
            data-testid="ai-chat-focus-toggle"
          >
            <Coins size={12} weight="bold" />
            <span className="ai-focus-toggle-label">Coin-Fokus: {focusSummary}</span>
          </button>
          <button className="ai-setup-toggle" onClick={toggleSetup} data-testid="ai-setup-toggle">
            Setup {showSetup ? <CaretUp size={12} /> : <CaretDown size={12} />}
          </button>
        </div>

        {/* Lern-Panel (collapsible) */}
        {showLearn && (
          <div className="ai-learn-panel" data-testid="ai-learn-panel">
            <div className="ai-learn-head">
              <span className="ai-learn-title">
                <Brain size={14} weight="fill" /> KI-Lernen · {(insights?.lessons || []).length} Lektionen
                {insights?.last_learn ? ` · zuletzt ${fmtTime(insights.last_learn)}` : ''}
              </span>
              <button className="ai-action-btn" onClick={learnNow} disabled={learning} data-testid="ai-learn-now-btn">
                <GraduationCap size={14} weight="bold" className={learning ? 'spin' : ''} />
                {learning ? 'Lernt…' : 'Jetzt lernen'}
              </button>
            </div>
            {insights?.stats?.totals && (
              <div className="ai-learn-stats" data-testid="ai-learn-stats">
                <span><b>{insights.stats.totals.signals}</b> Signale</span>
                <span>Winrate <b>{insights.stats.totals.signal_win_rate}%</b></span>
                <span>Paper-PnL <b>{(insights.stats.trades?.paper?.pnl ?? 0).toFixed(2)}</b> USDT</span>
                <span>Live-PnL <b>{(insights.stats.trades?.live?.pnl ?? 0).toFixed(2)}</b> USDT</span>
                <span><b>{insights.stats.totals.closed_trades ?? 0}</b> Trades geschlossen</span>
              </div>
            )}
            {insights?.assessment && <div className="ai-learn-assessment">{insights.assessment}</div>}
            {(insights?.lessons || []).length > 0 ? (
              <ol className="ai-lesson-list" data-testid="ai-lesson-list">
                {insights.lessons.map((l, i) => <li key={i}><b>{l.title}</b>: {l.detail}</li>)}
              </ol>
            ) : (
              <div className="ai-learn-empty">
                Noch keine Lektionen – die KI lernt automatisch aus geschlossenen Trades &amp; Signal-Ergebnissen
                (nach Trade-Close, täglich um Mitternacht und manuell über „Jetzt lernen“).
              </div>
            )}
          </div>
        )}

        {/* Setup (collapsible) */}
        {showSetup && (
          <div className="ai-setup" data-testid="ai-setup-panel">
            <label>
              <span>KI-Modell</span>
              <select
                value={modelValue}
                onChange={e => {
                  const [provider, model] = e.target.value.split('|');
                  updateConfig({ provider, model });
                }}
                data-testid="ai-model-select"
              >
                {MODEL_OPTIONS.map(o => (
                  <option key={o.model} value={`${o.provider}|${o.model}`}>{o.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Analyse-Intervall</span>
              <select value={cfg.interval_min || 10} onChange={e => updateConfig({ interval_min: Number(e.target.value) })} data-testid="ai-interval-select">
                {[5, 10, 15, 30, 60].map(v => <option key={v} value={v}>{v} min</option>)}
              </select>
            </label>
            <label>
              <span>Min. Konfidenz</span>
              <select value={cfg.min_confidence || 65} onChange={e => updateConfig({ min_confidence: Number(e.target.value) })} data-testid="ai-confidence-select">
                {[50, 60, 65, 70, 75, 80, 90].map(v => <option key={v} value={v}>{v}%</option>)}
              </select>
            </label>
            <label>
              <span>Trade-Cooldown</span>
              <select value={cfg.cooldown_min ?? 45} onChange={e => updateConfig({ cooldown_min: Number(e.target.value) })} data-testid="ai-cooldown-select">
                {[0, 15, 30, 45, 60, 120].map(v => <option key={v} value={v}>{v === 0 ? 'aus' : `${v} min`}</option>)}
              </select>
            </label>
            <label title="Wie viele KI-Trader-Trades dürfen pro Coin gleichzeitig offen sein (1–5). Nur der KI-Trader nutzt dieses Limit; andere Strategien bleiben bei 1 Trade pro Coin.">
              <span>Max. Trades pro Coin</span>
              <select value={cfg.max_trades_per_coin || 1}
                onChange={e => updateConfig({ max_trades_per_coin: Number(e.target.value) })}
                data-testid="ai-max-trades-select">
                {[1, 2, 3, 4, 5].map(v => <option key={v} value={v}>{v} Trade{v > 1 ? 's' : ''}</option>)}
              </select>
            </label>
            <label className="ai-setup-check">
              <span><Newspaper size={13} /> News</span>
              <input type="checkbox" checked={cfg.news_enabled !== false}
                onChange={e => updateConfig({ news_enabled: e.target.checked })} data-testid="ai-news-toggle" />
            </label>
            <label title="Darf die KI ihre eigenen Trade-Einstellungen (SL, TP, Hebel, …) ändern? Der investierte Betrag ist IMMER gesperrt.">
              <span><Sliders size={13} /> Autonomie</span>
              <select value={cfg.autonomy || 'suggest'} onChange={e => updateConfig({ autonomy: e.target.value })} data-testid="ai-autonomy-select">
                {AUTONOMY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label>
              <span>Lern-Zeitraum</span>
              <select value={cfg.learning_lookback_days || 14} onChange={e => updateConfig({ learning_lookback_days: Number(e.target.value) })} data-testid="ai-lookback-select">
                {[7, 14, 30, 60].map(v => <option key={v} value={v}>{v} Tage</option>)}
              </select>
            </label>
            <label>
              <span>Max. Lektionen</span>
              <select value={cfg.max_lessons || 10} onChange={e => updateConfig({ max_lessons: Number(e.target.value) })} data-testid="ai-max-lessons-select">
                {[5, 10, 15, 20, 25, 30, 40, 50].map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </label>
            <label className="ai-setup-check" title="Selbst-Lernen aus Signal-/Trade-Ergebnissen">
              <span><GraduationCap size={13} /> Lernen</span>
              <input type="checkbox" checked={cfg.learning_enabled !== false}
                onChange={e => updateConfig({ learning_enabled: e.target.checked })} data-testid="ai-learning-toggle" />
            </label>
            <label className="ai-setup-check" title="Automatischer Lernlauf nach jedem geschlossenen KI-Trade (max. 1x pro 15 min)">
              <span>Lernen nach Trade</span>
              <input type="checkbox" checked={cfg.learn_on_trade_close !== false}
                onChange={e => updateConfig({ learn_on_trade_close: e.target.checked })} data-testid="ai-learn-on-close-toggle" />
            </label>
            <label className="ai-setup-check" title="SL/TP aus der KI-Analyse direkt für die Order verwenden (statt der Coin-Trade-Settings)">
              <span>KI-Levels für Orders</span>
              <input type="checkbox" checked={cfg.use_ai_levels === true}
                onChange={e => updateConfig({ use_ai_levels: e.target.checked })} data-testid="ai-levels-toggle" />
            </label>
          </div>
        )}

        {/* Offene Einstellungs-Vorschläge der KI */}
        {proposals.length > 0 && (
          <div className="ai-proposals-strip" data-testid="ai-proposals-strip">
            <div className="ai-proposals-title">
              <Sliders size={13} weight="bold" /> Einstellungs-Vorschläge der KI ({proposals.length})
            </div>
            {proposals.map(p => (
              <div key={p.id} className="ai-proposal-card" data-testid="ai-proposal-card">
                <div className="ai-prop-head">
                  <b className="ai-prop-sym">{p.symbol === 'ENGINE' ? 'Engine' : coinLabel(p.symbol)}</b>
                  <span className="ai-prop-changes">
                    {Object.entries(p.changes || {}).map(([k, v]) => (
                      <span key={k} className="ai-prop-chg">
                        {k}: <s>{String(p.current?.[k])}</s> → <b>{String(v)}</b>
                      </span>
                    ))}
                  </span>
                </div>
                {p.reason && <div className="ai-prop-reason">{p.reason}</div>}
                <div className="ai-prop-actions">
                  <button className="ai-prop-approve" onClick={() => decideProposal(p.id, 'approve')} data-testid={`ai-proposal-approve-${p.symbol}`}>
                    <CheckCircle size={14} weight="fill" /> Übernehmen
                  </button>
                  <button className="ai-prop-reject" onClick={() => decideProposal(p.id, 'reject')} data-testid={`ai-proposal-reject-${p.symbol}`}>
                    <XCircle size={14} weight="fill" /> Ablehnen
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Coin-Fokus – Coin-Auswahl für den Chat, nur bei geöffnetem Toggle sichtbar
            (Toggle sitzt in der Status-Row neben „Lernen"). Eingeklappt = 0px, verdeckt nichts. */}
        {showChatFocus && (
          <div className="ai-decisions-wrap" data-testid="ai-coin-selector">
            <div className="ai-chat-focus-bar">
              <span className="ai-coin-selector-title">
                KI-CHAT FOKUS
                <span className="ai-coin-selector-sep">·</span>
                <span className="ai-coin-selector-mode">
                  {allSelected ? 'ALLE COINS' : `AUSGEWÄHLTE COINS (${chatCoins.length})`}
                </span>
              </span>
              <button
                className="ai-coin-all-toggle"
                onClick={toggleAll}
                title={allSelected
                  ? 'Nur den aktuell geöffneten Coin in den KI-Chat-Fokus nehmen'
                  : 'Alle Coins in den KI-Chat-Fokus nehmen'}
                data-testid="ai-coin-select-all"
              >
                {allSelected ? 'Nur aktueller Coin' : 'Alle Coins'}
              </button>
            </div>
            <div
              className="ai-decisions-strip"
              data-testid="ai-decisions-strip"
              ref={stripRef}
              onMouseDown={onStripMouseDown}
              onMouseMove={onStripMouseMove}
              onMouseUp={endStripDrag}
              onMouseLeave={endStripDrag}
            >
              {orderedCoins.map(coin => {
                const d = decisionFor(coin);
                const active = allSelected || chatCoins.includes(coin);
                const isCurrent = coin === selectedCoin;
                return (
                  <button
                    key={coin}
                    className={`ai-chip ${actionClass(d?.action)} ${active ? 'selected' : ''} ${isCurrent ? 'current' : ''}`}
                    onClick={() => { if (!stripDrag.current.moved) toggleCoin(coin); }}
                    title={d?.reasoning || (isCurrent ? 'Aktuell geöffneter Coin' : 'Anklicken, um den Coin für den KI-Chat auszuwählen')}
                    data-testid={`ai-coin-chip-${coin}`}
                  >
                    <span className="ai-chip-sym">{coinLabel(coin)}</span>
                    <span className="ai-chip-action">{d?.action || '–'}</span>
                    {d && <span className="ai-chip-conf">{d?.confidence ?? 0}%</span>}
                    {d?.signaled && <span className="ai-dec-signaled" title="Signal ausgelöst"><Lightning size={11} weight="fill" /></span>}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Chat + Input – im Lernen-Tab komplett ausgeblendet */}
        {!showLearn && (
        <>
        <div className="ai-chat-area" data-testid="ai-chat-area" ref={chatAreaRef} onScroll={onChatScroll}>
          {(() => {
            // Neueste angepinnte Summary ganz oben anzeigen, aus dem Haupt-Stream entfernen.
            // Dedupe (defensiv):
            //  (1) exakt-selbe id aus dem Stream entfernen
            //  (2) andere gepinnte Summaries desselben Tages entfernen
            //      – so bleibt garantiert genau eine sichtbare Tages-Zusammenfassung
            //        oben, selbst falls das Backend die Summary sowohl über die
            //        garantierte Pin-Rückgabe als auch im normalen Fenster liefert.
            const pinnedSummary = [...messages]
              .filter(m => m.role === 'summary' && m.pinned)
              .sort((a, b) => new Date(b.ts) - new Date(a.ts))[0];
            const pinnedId = pinnedSummary?.id;
            const pinnedDay = pinnedSummary?.day;
            const streamMessages = pinnedSummary
              ? messages.filter(m => {
                  if (m.id && m.id === pinnedId) return false;
                  if (m.role === 'summary' && m.pinned && m.day && m.day === pinnedDay) return false;
                  return true;
                })
              : messages;
            return (
              <>
                {pinnedSummary && (
                  <div className="ai-summary-pin-wrap" data-testid="ai-summary-pinned">
                    {renderMessage(pinnedSummary)}
                  </div>
                )}
                {streamMessages.length === 0 && !streaming && !pinnedSummary && (
                  <div className="ai-chat-empty">
                    <Robot size={36} weight="light" />
                    <p>Sag der KI, worauf sie achten soll – z.B.<br />
                      <em>„Achte auf den BTC-Support bei 60k"</em> oder <em>„Sei heute defensiv, nur Longs".</em><br />
                      Jede Nachricht fließt in die nächste Analyse ein.</p>
                  </div>
                )}
                {streamMessages.map(renderMessage)}
              </>
            );
          })()}
          {streaming && (
            <div className="ai-msg ai-msg-assistant">
              <div className="ai-msg-bubble">{streamText || <span className="ai-typing">KI denkt nach…</span>}</div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <div className="ai-quick-prompts" data-testid="ai-quick-prompts">
          {QUICK_PROMPTS.map(q => (
            <button key={q} className="ai-quick-chip" onClick={() => setInput(q)} disabled={streaming}>{q}</button>
          ))}
        </div>
        <div className="ai-input-row">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') sendMessage(); }}
            placeholder="Anweisung oder Frage an die KI…"
            disabled={streaming}
            data-testid="ai-chat-input"
          />
          <button className="ai-send-btn" onClick={sendMessage} disabled={streaming || !input.trim()} data-testid="ai-chat-send-btn">
            <PaperPlaneRight size={16} weight="fill" />
          </button>
          <button className="ai-icon-btn" onClick={clearChat} title="Chat leeren" data-testid="ai-chat-clear-btn">
            <Trash size={15} />
          </button>
        </div>
        </>
        )}
        <div className="ai-panel-footer">
          Auto-Trading pro Coin über das <Lightning size={11} weight="fill" color="#FFD60A" />-Symbol am „KI Trader"-Tab konfigurieren (Paper/Live).
        </div>
      </div>
    </div>
  );
};

export default AITradingPanel;
