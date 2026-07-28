# PRD – Daytrading-Website (extern/ausgelagert, dynamischeStrat)

## Original-Problemstatement (Juni 2026)
Bestehende, produktiv laufende Daytrading-Website (GitHub: AntonHeinrich05/dynamischeStrat,
Branch 28071303-weiterebugfixes) soll verbessert werden:
- Regime-Erkennung prüfen (wird Seitwärts wirklich seitwärts erkannt?) + Regime am Chart anzeigen
- Übersicht/Funktion: Regime für Timeframe/Zeitraum/Coins suchen & unter der Konfiguration speichern;
  beides: Regime für alle Coins zusammengefasst UND je Coin einzeln; Ähnlichkeit vergleichen
- Regime behalten/verwerfen können
- Strategie-Discovery + Optimierer direkt für EIN ausgewähltes Regime (alle Einstellungen:
  Indikator-Auswahl, Iterationen, Ziel, Min-Trades, Regeln, Trade-Räume, Timeframe)
- Top-5-Ergebnisse, Auswahl bestätigen, Regime für Regime durchgehen, dann dynamische Strategie bauen
- Finale dynamische Strategie per Walk-Forward auf unangetastetem Holdout testen (kein Lookahead,
  identisch zu Live/Paper)
- Später: Walk-Forward-Verbesserung des normalen Optimizers (mehr Zeit, sinnvollere Kombinationen)

Grundsatz des Nutzers: Website läuft produktiv – Änderungen sauber, modular, rückwärtskompatibel,
in bestehende Architektur einfügen, sehr customizable.

## Architektur
- Backend: FastAPI (Port 8001), Router-Module in backend/routers, Services in backend/services,
  MongoDB via MONGO_URL. Marktdaten: Bitunix (ohne API-Key). Admin-Auth: JWT, Passwort "admin".
- Frontend: React (CRA + craco), recharts, Overlay-Panels (Optimizer, Backtester, NEU: Regime-Lab).
- Bestehende Regime-Logik: services/regime.py (K-Means, rückblickende Features, kein Lookahead),
  services/dynamic_strategy.py (Segmente, Discovery/Optimierung je Regime), routers/dynamic.py.

## Umgesetzt (28.07.2026) – Regime-Lab (komplett getestet, Iteration 1+2)
1. Regime-Label-Fix (services/regime.py): Beschriftung jetzt aus ROHEN Feature-Mitteln
   (Trendstärke = |Trend|/Volatilität statt z-Score) → Seitwärts ist wirklich seitwärts.
   Zusätzlich stats je Regime (trend_pct/Tag, vol, Effizienz, Trendstärke).
   relabel_regimes(): Migration alter gespeicherter Modelle beim Laden.
2. services/regime_lab.py: Analyse-Jobs – Regime clustern (kombiniert + je Coin), Segmente
   + komprimierter Kursverlauf gespeichert (Mongo regime_analyses), Coin-Ähnlichkeitsmatrix,
   Holdout via train_pct (Modell nur auf Trainingsteil), Reuse-Helfer (regime_ranges,
   segments_from_ranges – auch für abweichenden Timeframe).
3. services/regime_opt.py: run_regime_optimizer (Discovery/Params/Combo NUR auf Abschnitten
   eines Regimes, Top-5, Walk-Forward innerhalb der Phase, Fallback: beste nicht-validierte
   Kombination wird markiert angeboten) + run_walkforward (Holdout-Test der zusammengestellten
   dynamischen Strategie vs. beste Einzelstrategie, Verdict, Equity-Punkte).
4. routers/regime_lab.py: /api/regime-lab/* (analyze, status, active, cancel, list, get [mit
   Label-Migration], delete, keep, optimize, assign, build [erzeugt dynamic_strategies-Doc,
   kompatibel mit bestehender Live-Umschaltung], walkforward). Verworfene Regime werden bei
   optimize/build/walkforward übersprungen; Validierung regime_id → 400.
5. Frontend: components/RegimeLab.js (Overlay, Workflow 1-4), RegimeChart.js (Preis + farbige
   Regime-Bänder + Holdout-Linie + Legende), RegimeOptimizePanel.js (alle Optimizer-Einstellungen
   je Regime, Top-5 mit "Für dieses Regime übernehmen"), RegimeLab.css; Header-Button (ChartScatter).

## Umgesetzt (28.07.2026, Teil 2) – Local-Worker-Support fürs Regime-Lab
- Alle Regime-Lab-Jobs (Analyse, Regime-Strategie-Suche, Walk-Forward) laufen wahlweise
  auf dem lokalen Worker: execution:"local" in analyze/optimize/walkforward.
- services/local_exec.py: Job-Typ "regime_lab" (_get_job, apply_result mit serverseitiger
  Persistierung via regime_lab.persist_worker_result), worker_supports_regime_lab(),
  REQUIRED_WORKER_VERSION 1.5.0, Versions-Gate im claim() (alte Worker bekommen keine
  regime_lab-Jobs – wichtig bei gemischter Flotte).
- services/regime_lab.py: persist_analysis/persist_worker_result; run_analysis db-los fähig
  (Worker schickt analysis_doc zurück). services/regime_opt.py: _load_doc (analysis_doc im
  Payload statt Mongo), Persist-Guards, Multi-Core-Pool (_make_seg_pool über parallel_sim,
  aktiv nur bei SIM_WORKERS>1 = lokaler Worker) für Optimierung UND Walk-Forward.
- routers/regime_lab.py: execution-Branch, 503 ohne Worker, 409 mit Update-Hinweis bei
  Worker < 1.5.0; Analyse-Doc (ohne chart) wird in den Job-Payload eingebettet.
- local_worker/worker.py: Version 1.5.0, handle_regime_lab (alle 3 fn), Cancel-Handling.
  Worker-Paket-Download bündelt services/*.py automatisch → Nutzer muss Paket 1x neu laden.
- Frontend RegimeLab.js: Ausführungs-Toggle Cloud/Lokal (mit Online-Punkt, Zahnrad öffnet
  LocalWorkerPanel), gilt für alle Regime-Lab-Jobs, in localStorage persistiert.
- E2E real verifiziert: Test-Worker im Pod (v1.5.0) hat Analyse, Multi-Core-Optimierung
  (8 Kerne) und Walk-Forward lokal gerechnet; Ergebnisse serverseitig persistiert; Jobs
  gingen NICHT an den parallel verbundenen alten Nutzer-Worker (v1.4.1).
- Iteration 3: Regression bestanden; 1 Frontend-Crash (execution nicht in AnalysisDetail
  destrukturiert) gefixt und per Screenshot verifiziert.

## Backlog / Nächste Aufgaben (priorisiert)
- P1: Walk-Forward des normalen Optimizers verbessern: mehr Zeit/Budget beim Indikator-Testen,
  sinnvollere Regel-Kombinationen (z.B. Trend+Volumen-Paare bevorzugen), bessere Ergebnisse
  bei langen Zeiträumen (Nutzer-Wunsch "später, erst Regime-Workflow").
- P1: Dynamische Strategie auf EINEN Coin optimieren (per_coin-Workflow ist im Regime-Lab
  bereits möglich – ggf. Shortcut/Empfehlung im UI).
- P2: Vergleich mehrerer Regime-Analysen (verschiedene Timeframes/Zeiträume nebeneinander).
- P2: Regime-Lab-Ergebnisse in das Lern-Gedächtnis (services/learning.py) einspeisen.
- P2: Lokaler Worker-Support für Regime-Lab-Jobs (aktuell Cloud/sequenziell).

## Test-Status
- iteration_1.json: 2 kritische Bugs gefunden (Label-Migration, kept-Filter) + 1 minor (500 statt 400) → alle gefixt.
- iteration_2.json: 4/4 fokussierte Backend-Tests bestanden, Regression ok. Frontend-Flows in Iteration 1 zu 100% bestanden.
- Demo-Analyse "Test-Analyse" (ra_c8206904, BTC/ETH, 15m, 60d, Training 75%) mit 2 bestätigten
  Strategien + Walk-Forward-Ergebnis ist als Beispiel gespeichert.
