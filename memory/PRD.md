# PRD – Krypto Daytrading Website (Krypto_Alert)

## Original-Problemstellung
Bestehende, produktiv laufende externe Daytrading-Website (React + FastAPI + MongoDB, Datenquelle Bitunix, optionaler lokaler Worker). Big Update gewünscht:
1. Bugfixes: Optimizer-Equity-Kurve, CSV-Export (Trades + Equity) wie im Backtester, Zeitfenster/Timeframe-Bug (5m eingestellt, 1m verwendet)
2. Robustheits-/Walk-Forward-System transparenter machen (Score-Aufschlüsselung, Aussortierungsgründe, Ranking-Begründung, Verlaufs-Lücken)
3. Dynamische Strategien: Marktregime automatisch erkennen (ohne Lookahead, Anzahl automatisch, max. einstellbar), pro Regime eigene Konfiguration, Vertrauenswert + Mindesthaltedauer gegen Flattern, IMMER Vergleich gegen statische Benchmark; normale Strategien müssen unverändert weiter funktionieren.
Grundsatz: Stabilität, Rückwärtskompatibilität, modulare Integration in bestehende Architektur.

## Architektur
- Backend: FastAPI (Port 8001, /api-Prefix), Router in `backend/routers/`, Services in `backend/services/`, Mongo via MONGO_URL
- Frontend: React (CRA/craco), Komponenten in `frontend/src/components/`
- Kern-Services: backtester.py (simulate_pair), optimizer.py (params/discovery/combo/dynamic), robustness.py (WF/DD/Konstanz/Stress/Stabilität/MC/Regime-Info), regime.py (NEU: K-Means-Regime-Erkennung), dynamic_strategy.py (NEU: Regime-Konfig-Optimierung + Benchmark-Vergleich)
- Lokaler Worker: local_worker/worker.py spiegelt Cloud-Jobs (Optimizer/Backtest)

## Umgesetzt (Session 27.07.2026)
### Phase 1 – Bugfixes
- Optimizer speichert Trades des besten Kandidaten während des Laufs (`_collect_best_trades`, `job["export_trades"]`, DB `optimizer_trades`) → Equity-Kurve kommt sofort aus gespeicherten Daten (`source=stored`), kein Timeout mehr; scope=all simuliert weiterhin live
- CSV-Export im Optimizer: GET /api/optimizer/export/{job_id}?kind=trades|equity + UI-Buttons (wie Backtester)
- Timeframe-Bug: applyParams sendet jetzt `timeframe`, Backend synchronisiert `strategy_timeframes` beim Apply (type=params); Zeitfenster-Optimierung (sessions) = Uhrzeit Berlin-Zeit, verifiziert korrekt
- Lokaler Worker liefert export_trades mit (worker.py + local_exec.py)

### Phase 2 – Transparenz
- robustness.py: `build_checks_summary`, `fail_reasons`, `rank_reason` → jeder Top-5-Kandidat hat checks[] (id/label/enabled/passed/value/detail/is_filter), fail_reasons[], rank_reason
- Verlauf (/api/optimizer/history): checks_passed/checks_enabled, fail_reasons, rank_reason; UI-Spalte "Checks"
- Params-Modus: search_stats (Algorithmus, Verbesserungs-Historie) im Ergebnis sichtbar
- UI: Check-Chips (✓/✗/ℹ) mit Mouseover-Details, Ranking-Begründung, Aussortierungsgründe pro Karte

### Phase 3 – Dynamische Strategien
- services/regime.py: Features (Trend, Volatilität, Effizienz, rel. Volumen; rein rückblickend = kein Lookahead), K-Means mit k-means++, Anzahl automatisch via Silhouette (2..max, einstellbar 3–10), zu kleine Regime werden gemergt, deutsche Regime-Labels, Online-Klassifikation mit Vertrauenswert + Umschalt-Sicherheit + Mindesthaltedauer (Anti-Flattern), `current_regime` für Live-Anzeige
- services/dynamic_strategy.py: Segment-Simulation mit Warmup, per-Regime Random-Search der Trade-Parameter (min. Trades pro Regime, sonst Fallback), statische Benchmark mit gleichem Suchbudget, Out-of-Sample-Vergleich, Verdict (dynamisch nur empfohlen wenn Test-PnL positiv + klar besser + DD ok); Regimewechsel schließt offene Positionen (dokumentiert)
- Optimizer mode="dynamic" (Router + Service), Ergebnis enthält model/regimes/configs/comparison/verdict; Equity+CSV über Gesamtverlauf
- routers/dynamic.py: save/list/refresh (aktuelles Regime + Sicherheit + Ähnlichkeiten + Info-Vergleich aller Konfigs über letzte X Tage)/apply (Coin-Overrides via strategy_coin_configs)/delete
- UI: Modus-Karte "Dynamische Strategie" mit Einstellungen (max. Regime, Merkmal-Fenster, Umschalt-Sicherheit, Mindesthaltedauer, Training-%), DynamicResult.js (Regime-Tabelle, Vergleich, Verdict, Speichern), DynamicPanel.js (Verwaltung, "Regime aktualisieren", "Konfiguration übernehmen")

## Umgesetzt (Session 27.07.2026, Teil 2 – Bugfixes + 4 Features)
### Bugfixes (vom User gemeldet)
- Lokaler Worker & Dynamik: alte Worker (<1.3.0) interpretierten mode=dynamic als Discovery → leeres Ergebnis. Fix: WORKER_VERSION 1.3.0, Server-Gate (409 mit Anleitung) bei execution=local + dynamic mit altem Worker, DynamicResult zeigt klaren Hinweis (data-testid dyn-missing) statt leerer Anzeige. User-Worker muss Paket neu laden (/api/localworker/package liefert immer aktuellen Code)
- Backend-Crash bei großen Tests: Equity-Fallback simulierte in der Cloud (2000 Tage × 10 Coins → OOM). Fix: RAM-Guard in _simulate_equity (days>120 oder days×coins>900 → 400 mit deutscher Meldung), Export-Rows auf 25000 gekappt (Mongo 16MB-Limit)
- Discovery-Indikatoren: verifiziert vorhanden (Lade-Race bei COINS)
### Feature: Auto-Regime-Umschaltung + Wechsel-Protokoll
- services/dynamic_live.py: refresh/apply/check_one/watch_loop (Hintergrund-Watcher, startet im Server-Lifespan); pro dynamischer Strategie: auto_check_enabled, auto_apply_enabled, check_interval_minutes, check_days (POST /api/dynamic/{id}/settings)
- Wechsel-Protokoll db.dynamic_switch_log (from/to, Sicherheit, Ähnlichkeiten, Begründung, auto_applied), GET /api/dynamic/{id}/log; UI: Auto-Prüfung/Auto-Übernahme-Toggles + Protokoll-Ansicht im DynamicPanel
### Feature: Regel-Varianten pro Regime (nur Custom-Strategien)
- dynamic_strategy.optimize_regime_rules: testet EINE zusätzliche Regel je Regime (Kandidaten aus gewählten Indikatoren, sortiert nach Lern-Gedächtnis), akzeptiert nur bei >10% Verbesserung; fließt in Dynamik-Backtest/Verdict ein (Live nutzt weiterhin Basis-Regeln + Trade-Parameter – dokumentiert im UI-Tooltip)
- UI: Checkbox dyn-rule-variants blendet Indikator-Auswahl im Dynamik-Modus ein; Variante als Pill in der Regime-Tabelle
### Feature: Lern-Gedächtnis
- services/learning.py: record_run (nach jedem Optimizer-Lauf, Cloud + lokal), indicator_weights (gewichtet Regel-Varianten-Kandidaten), summary; db.learning_memory; GET /api/learning/summary; UI: LearningPanel im Optimizer

## Test-Status
- Iteration 12: Backend 23/23, Frontend verifiziert (Grundfeatures)
- Iteration 13: Backend 14/14 (Bugfixes + Features), Frontend verifiziert; 1 Bug (LearningPanel nicht gerendert) → gefixt
- Iteration 14: LearningPanel-Fix verifiziert
- User-Worker "Anton-PC" v1.3.0 bereits verbunden

## Bekannte Minor-Punkte (vorbestehend, nicht blockierend)
- Console: 401/Failed-to-fetch vor Admin-Login (fetchNotif/fetchSession)
- React-Warnung: <span> in <option> in einem Select

## Backlog / Nächste Schritte
- P1: Regel-Varianten auch live nutzbar machen (Variante als abgeleitete Custom-Strategie speichern + per Coin-Toggle umschalten)
- P2: Benachrichtigung (Notification) bei automatischem Regime-Wechsel
- P2: Lern-Gedächtnis: TTL/Begrenzung + Nutzung auch für Discovery-Priorisierung
- P2: Regime-Auto-Anzahl zusätzlich mit Gap-Statistik validieren
