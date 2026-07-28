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

## Umgesetzt (Session 28.07.2026 – Kerzen-Refactor, Worker v1.4.0, Sub-Strategien pro Marktphase)
Nutzer-Meldung: Worker stürzt beim Laden heruntergeladener Kerzen ab, 5400 Tage unmöglich,
Tests zu langsam, keine guten dynamischen Strategien, Marktphase nicht erkennbar.
Nutzer-Priorität: Worker-Bug + Performance; Regime-Erkennung live UND im Backtest.

### Ursache des Worker-Absturzes
Kerzen wurden als `List[Dict]` gehalten (~450 Byte/Kerze). 5400 Tage 1m = 7,78 Mio. Kerzen
≈ 3,3 GB RAM + minutenlanges Entpacken aus `.pkl.gz` → Swap/OOM, Heartbeat-Ausfall,
und ein abgestürzter Job meldete gar nichts zurück (UI hing bis Timeout).

### Fix: spaltenbasierte Kerzen (numpy)
- NEU `services/candles.py` – `CandleArray` (ts/open/high/low/close/volume als numpy-Arrays),
  duck-typed zu `List[Dict]` (`len`, `ca[i]`→dict, `ca[a:b]`→CandleArray, Iteration)
- NEU `services/vec.py` – vektorisierte EMA/RSI/ATR/Heikin-Ashi, numerisch identisch zur Referenz
- `candle_cache`: Disk-Format `.npy` (Legacy `.pkl.gz` wird automatisch migriert),
  `disk_meta()` per memmap, paralleler Download mit Ratelimit-Pacing (3–6 Ströme)
- Vektorisiert: `timeframes.aggregate_candles`, `regime.compute_features`/`classify_series`
  (+ neue `classify_matrix`), `robustness.classify_regimes`
- Spalten-Hotpath in `backtester.simulate_pair`, `compute_levels`, `_clip_history`;
  `fast_sim.FastSeries` nutzt die Arrays direkt
- Messwerte 5400 Tage 1m: **373 MB statt ~3,3 GB**, Laden 0,1 s, Aggregation 5m 0,2 s,
  Simulation 1,7 s. Verifiziert: identische Trades/PnL wie mit Dict-Listen.

### Lokaler Worker v1.4.0
- `handle_backtest`/`handle_optimizer` fangen jetzt jede Exception ab und melden sie
  zurück (inkl. verständlicher MemoryError-Meldung) – Jobs sterben nicht mehr still
- `_post_result` mit 5 Wiederholversuchen
- Daten-Download in 360-Tage-Etappen, nach jeder Etappe gespeichert → abbruchsicher/resume
- `DataIndex.summary()` gecacht (10 s), `build_missing()` liest nur Header per memmap
- `sys.setswitchinterval(0.002)` für stabilen Heartbeat
- RAM-Limit-Umrechnung 1 MB ≈ 20.000 Kerzen (vorher 2.000)
- Server: `REQUIRED_WORKER_VERSION = 1.4.0`, `workers_public().outdated`,
  `/api/localworker/status.required_version`; UI-Warnung bei veraltetem Worker
- Timeouts: Heartbeat 45→90 s, Queue 180→300 s, Stillstand 300→900 s

### Dynamische Strategien: eigene Sub-Strategie pro Marktphase (P1 des Nutzers)
- Neue Option `dynamic.per_regime_strategies` – pro Regime läuft eine **vollständige
  Regel-Discovery** (`dynamic_strategy.discover_regime_strategy`) plus eigene
  Trade-Parameter; Ergebnis sind N eigenständige Sub-Strategien
- Zusatzoptionen `max_rules_per_regime`, `start_from_base`
- **Walk-Forward innerhalb jeder Marktphase**: `split_segments()` teilt die Abschnitte
  einer Phase in Training/Validierung; `validation_passed()` verlangt Profitabilität +
  Mindest-Trades auf dem unbekannten Teil. Besteht nichts → Basis-Strategie bleibt aktiv
- `optimize_regime` prüft die Top-8-Konfigurationen gegen die Validierung
- Ergebnis enthält `sub_strategies`, `validation`, `validation_passed`, `regime_walk_forward`
- `/api/dynamic/save` speichert `sub_strategies`; Live-State liefert `active_sub_strategy`

### Marktphasen-Anzeige (live)
- NEU `GET /api/dynamic/current-regime` (`dynamic_live.detect_current`) – aktuelle Phase
  eines Coins ohne gespeicherte Strategie
- UI-Karte „Aktuelle Marktphase" im Dynamik-Panel: Coin/Timeframe/Zeitraum, aktuelle Phase,
  Sicherheit, Anteile aller Phasen, Zeitleiste der letzten Wechsel

### Dokumentation
- `/app/ANLEITUNG_DYNAMISCHE_STRATEGIEN.md`: Bedienung, Walk-Forward-Logik,
  Worker-Update, „Warum finde ich keine guten Strategien?"

### Offen / Backlog
- P0: Live-Scanner schaltet bei Regimewechsel noch nicht auf die Sub-Strategie-Regeln um
  (Backtest + Anzeige sind fertig)
- P0: Worker-Selbstaktualisierung (Code-Update ohne manuelles Zip)
- P1: Rolling-Walk-Forward auch je Marktphase; Sub-Strategie als eigene Custom-Strategie exportierbar
- P1: Restzeit-Schätzung für lange Läufe
- P2: GPU-Pfad (CuPy) im Worker; `Optimizer.js` (>1280 Zeilen) zerlegen


## Umgesetzt (Session 28.07.2026 – Local-Worker-Stabilität, Nachlage v1.3.2)
### Bugfix: Worker geht offline während der Simulation vorbereitet wird
- Nach Fix v1.3.1 (Disk-IO in Thread) blieb das Problem: `run_optimizer`/`run_backtest` machen viel synchrone Numpy-/Pandas-Arbeit (FastSeries-Init über hunderttausende Kerzen, `aggregate_candles`, `gc.collect()`) direkt im Haupt-Event-Loop des Workers → Poll-Heartbeat fällt für Sekunden aus → Server markiert offline → nächster Start läuft auf Cloud.
- Fix v1.3.2: neue Helferfunktion `_run_isolated(coro_factory)` startet die komplette Rechen-Coroutine in einem **eigenen Thread mit eigener Event-Loop** (`asyncio.to_thread` + `loop.run_until_complete`). Der Haupt-Loop des Workers ist damit während der Simulation völlig frei und pollt stabil weiter.
- Angewendet in `handle_backtest` und `handle_optimizer`. Cancel-Flag und Progress bleiben unverändert (Shared-Dict `opt.JOBS`/`bt.JOBS`).

## Umgesetzt (Session 28.07.2026 – Local-Worker-Stabilität)
### Bugfix: Worker trennt sich beim Laden großer Kerzen-Caches
- Symptom: Nach dem Laden lokal gespeicherter Kerzen (gzip+pickle) für einen Strategie-Test war die asyncio-Loop des Workers je Symbol mehrere Sekunden blockiert → Heartbeat fiel aus → Server markierte den Worker als offline → nächster Strat-Test lief über die Cloud statt lokal.
- Fix: `candle_cache._load_disk`/`_save_disk` werden jetzt konsequent via `asyncio.to_thread` aufgerufen. `get_candles` nutzt das für den Disk-Hydrate, es gibt `persist_symbol_async()` und `_evict_if_needed_async()`. `local_worker/worker.py` verwendet die async-Varianten überall (handle_backtest / handle_optimizer / handle_data_job / auto_update_loop) und wickelt zusätzlich `index.update_from_cache(...)` in `asyncio.to_thread` ab.
- Kulanteres Timing im Server: `WORKER_TIMEOUT` 20→45s, `QUEUED_TIMEOUT` 90→180s, `STALE_TIMEOUT` 240→300s (`services/local_exec.py`) → einzelne langsame Uploads/IO-Peaks führen nicht mehr zu einem sofortigen „offline"-Flackern.
- Worker-Version 1.3.0 → 1.3.1. Wichtig: User muss das Worker-Paket neu laden (`/api/localworker/package` → ⚙ Verwalten → Download), sonst greift der Fix im Worker nicht.

## Umgesetzt (Session 27.07.2026 – Teil 1)
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
