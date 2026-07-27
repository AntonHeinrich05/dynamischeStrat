# PRD – Krypto Alert / Daytrading-Website (Fork von NEW25.07)

## Original-Problemstellung (26.06.2026 / Session 26.07.2026)
Bestehende, produktiv laufende Daytrading-Website (Repo dean06greif-ai/Krypto_Alert, Branch NEW25.07).
Lokale Backtests / Strat-Optimierer / Strat-Finder verbessern – sauber, modular, rückwärtskompatibel,
sehr customizable. Konkret gefordert:
1. Walk-Forward-Modus als zusätzliche Einstellung bei Strat Finder / Optimierer / Kombi (Default 75% Training / 25% Test),
   Bewertung bevorzugt Strategien mit ähnlich guter Performance auf Training UND Test (Overfitting-Schutz, WF-Score).
2. Drawdown-Filter: max. Drawdown relativ zum PnL (Default 40%), gilt für Finder, Optimierer und Walk-Forward.
3. Konstanz-Test: Zeitraum in Abschnitte teilen (einstellbar, Default 30 Tage), max. Abweichung einstellbar (Default 20%),
   zu schwankende Strategien aussortieren.
4. Immer Top-5-Ergebnisse anzeigen, User wählt aus, welche Strategie übernommen wird.
5. GPU-Unterstützung für Local Mode (NVIDIA, Auto-Erkennung, CPU-Fallback).
6. Zeitraum-Auswahl in 360-Tage-Schritten bis 15 Jahre (5400 Tage) erweitern.

## Architektur
- Backend: FastAPI (/app/backend), MongoDB (Motor), Router + Services + Strategies.
- Frontend: React CRA/craco (/app/frontend), deutschsprachige UI, Phosphor-Icons.
- Local Worker: /app/local_worker/worker.py – Outbound-Polling, nutzt identischen services/-Code.
- Auth: JWT, Admin über backend/.env (ADMIN_USER=Admin, ADMIN_PASSWORD=admin).
- Echte Marktdaten (Bitunix), kein Mock.

### Neue Module (26.07.2026)
- `backend/services/robustness.py`: parse_config, split_histories, walk_forward_eval (WF-Score,
  Konsistenz = min/max der zeitnormierten Qualität, Score = Mittel × (0.4+0.6×Konsistenz), negativ wenn
  eine Seite verliert), dd_check (DD/PnL-Ratio, PnL<=0 fällt durch), collect_chunk_pnls + evaluate_chunks
  (Konstanz: std/mean der Abschnitts-PnLs in %), TopTracker (dedupe per rule_key).
- `backend/services/gpu_accel.py`: CuPy-Erkennung (USE_GPU=1 + cupy), GPU-Kernels für rolling
  mean/std/max/min mit CPU-Fallback (pandas-identisch). Genutzt von fast_sim (SMA, Bollinger, Stochastik).
  EMA/RSI/MACD + Trade-Simulation bleiben bewusst CPU (rekursiv/ereignisbasiert).

### Integration (services/optimizer.py)
- Body-Felder: walk_forward{enabled,train_pct}, dd_filter{enabled,max_dd_pct}, constancy{enabled,chunk_days,max_deviation_pct}.
- WF-Split VOR fs_map/Prozess-Pool → gesamte Suche läuft auf Trainingsdaten.
- _score(..., dd_max_pct): DD-Verletzer bekommen -5e8-Malus (opt-in).
- TopTracker wird in _discover/_refine/_optimize_trade_settings gefüllt; params-Modus nutzt die top-Liste.
- _finalize_top5: Top-~10 Kandidaten → Test-Evaluierung (WF), DD-Check (Training UND Test), Konstanz-Test,
  Re-Ranking (bestanden zuerst; bei WF nach wf_score, sonst score), Fallback = bestes Suchergebnis.
- result: top5[], walk_forward{train_days,test_days,train_pct}, robustness{Config-Echo}. Alles additiv/rückwärtskompatibel.
- days-Clamp 1500 → 5500 (auch routers/backtest.py, routers/local_worker.py).

### Frontend (Optimizer.js)
- Sektion "ROBUSTHEIT & WALK-FORWARD" (Toggles opt-wf-toggle/opt-dd-toggle/opt-ct-toggle + Eingaben
  opt-wf-trainpct/opt-dd-maxpct/opt-ct-chunkdays/opt-ct-maxdev, Split-Info opt-wf-split-info), persistiert in localStorage.
- Top-5-Karten (opt-top5, opt-top5-0..4) mit WF-Score/Konsistenz, DD/PnL-Badge, Konstanz-Badge,
  Training-/Test-Metriken, Regeln/Parameter-Pills; Klick wählt aus, Übernehmen/Speichern nutzt selEntry.
- DAY_OPTIONS bis 5400 (auch Backtester.js, LocalWorkerPanel DL_DAYS).
- LocalWorkerPanel: use_gpu-Select aktiviert, GPU-Status im Worker-Header (aktiv/aus).
- Worker v1.2.0: gpu_info via gpu_accel/CuPy (torch-Fallback), USE_GPU aus Website-Einstellung.

## Was wurde umgesetzt (26.07.2026)
- [x] Repo geklont, Umgebung eingerichtet (.env neu erstellt – waren nicht im Repo), Services laufen.
- [x] Features 1–6 komplett (siehe oben), alles optional & rückwärtskompatibel.
- [x] **Rolling Walk-Forward** (2. Session): walk_forward.mode single|rolling + windows (2–12, Default 4).
      robustness.rolling_windows (gleitende Fenster mit ISO-Datums-Ranges), aggregate_rolling
      (Ø WF-Score, Ø Konsistenz, % positive Fenster), combine_test_metrics (PnL summiert, DD = schlechtestes
      Fenster). Suche läuft auf Fenster-1-Training; Top-Kandidaten werden über alle Fenster geprüft.
      UI: Umschalt-Buttons "Einfacher Split"/"Rolling" (opt-wf-mode-single/-rolling), Fenster-Anzahl
      (opt-wf-windows), Karten zeigen Fenster-Chips (opt-wf-windows-{i}) mit Test-PnL + Tooltip (Datum, Train-PnL, WF-Score).
- [x] **Anchored Walk-Forward** (3. Variante, 26.07.2026): walk_forward.mode="anchored" –
      Training beginnt immer am Anfang und wächst je Fenster (rolling_windows(anchored=True)),
      Test-Segmente identisch zum Rolling (gleiche OOS-Abdeckung), zeitnormierte Bewertung
      berücksichtigt die wachsende Trainingslänge. UI: 3. Umschalt-Button (opt-wf-mode-anchored),
      eigene Split-Info + Ergebnis-Tag. Phase: "Anchored Walk-Forward: Kandidat i/n · Fenster w/W".
- [x] **WF-Historie / Verlauf** (26.07.2026, 4. Session): GET /api/optimizer/history (kompakte
      Robustheits-Kennzahlen je Lauf) + GET /api/optimizer/result/{job_id} (alten Lauf laden).
      UI: "Verlauf"-Button (opt-history-toggle) neben Start -> Tabelle (opt-history-row-{i}) mit Datum,
      Modus+WF-Variante, PnL, WR, WF-Score (Farbbalken), Konsistenz, Test-PnL, DD/PnL, Konstanz, Filter;
      Klick laedt den kompletten alten Lauf inkl. Top-5 in die Ansicht.
- [x] **Multi-Coin-Check** (26.07.2026, 4. Session): Bei >1 Coin wird jeder Top-5-Kandidat je Coin einzeln
      bewertet (entry.per_symbol + positive_symbols_pct); UI zeigt PnL-je-Coin-Chips (opt-per-symbol-{i}).
- [x] **Phasen-Transparenz** (2. Session): Bei aktivem WF sind alle Such-Phasen mit "Training · " geprefixt;
      Finalize zeigt "Walk-Forward-Test: Kandidat i/n auf X Tagen unbekannter Testdaten",
      "Rolling Walk-Forward: Kandidat i/n · Fenster w/W" und "Konstanz-Test: Kandidat i/n (Xd-Abschnitte)".
- [x] Unit-/Regressionstests: test_robustness_features.py (25 Tests inkl. 5 Rolling-Tests) +
      test_iter11_robustness.py (6) – alle grün. E2E (curl, echte Bitunix-Daten) + UI-Screenshots verifiziert.
- [x] Bugfixes nach Testing-Agent: tracker an _discover übergeben, Top-5-Fallback wenn alle Kandidaten
      unter Min-Trades, Filter-Verletzer werden angezeigt & geflaggt statt versteckt.
- [x] Doku: local_worker/README.md GPU-Abschnitt, requirements-Hinweis (cupy-cuda12x/11x).

## Bekannte Punkte / Nicht-Regressionen
- tests/test_winrate_bug.py::test_winrate_bugfix_full_flow erwartet "Re-hydrated"-Logzeile – schlägt in
  frischer Umgebung ohne persistierte Trades fehl (Alt-Test, umgebungsabhängig, keine Code-Regression).
- Kosmetisch (vorbestehend, Iter10): React-Warnung <span> in <option> im opt-days-Select.
- GET /api/localworker/settings liefert {settings:{...}} (vorbestehendes Format).
- GPU-Wirkung konnte im Pod nicht real gemessen werden (keine NVIDIA-GPU) – CPU-Fallback getestet
  (Ergebnisse identisch zu pandas). Realistischer Nutzen: Indikator-Vorberechnung bei großen Zeiträumen;
  Multi-Core (SIM_WORKERS) bleibt der größte Hebel.

## Backlog / Nächste Schritte
- P1: Rolling Walk-Forward (mehrere Train/Test-Fenster statt einem Split) als Erweiterung.
- P1: Top-5 auch für lokalen Worker-Pfad end-to-end mit echtem Worker verifizieren (Code identisch, Worker nutzt gleiche services/).
- [x] ~~WF-/Konstanz-Verlauf visualisieren~~ (erledigt 26.07.2026, 4. Session)
- P2: GPU-Beschleunigung für Batch-Regelauswertung (viele Kandidaten gleichzeitig auf GPU) evaluieren.
- P2: Kosmetik: <option>-Warnung beheben; localworker/settings-Format vereinheitlichen.

## Robustheits-Checks 2. Welle (erledigt 26.07.2026, 5. Session)
Alle 4 als optionale Toggles im Optimizer integriert (Body-Felder / robustness.py / _finalize_top5):
- [x] 1. Kosten-Stresstest: stress_test{enabled, cost_multiplier=1.5} -> Kandidat wird mit
      vervielfachtem fee_percent erneut bewertet, muss profitabel bleiben. entry.stress, Badge "Stress x1.5".
- [x] 3. Parameter-Stabilität: stability{enabled, variation_pct=10} -> 4 Varianten (alle numerischen
      Schwellen/Params +-var, +-var/2), >=50% müssen profitabel bleiben. entry.stability (positive_pct,
      retention_pct), Badge "Stabil X%".
- [x] 2. Monte-Carlo: monte_carlo{enabled, runs=200, max_dd_p95_pct=100} -> Trade-Reihenfolge mischen
      (Seed 42, deterministisch), DD-Verteilung p50/p95/worst; p95-DD <= 100% vom PnL. entry.monte_carlo,
      Badge "MC-DD p95".
- [x] 4. Regime-Analyse: regime_analysis{enabled} -> SMA-Steigungs-Klassifikation (bull/bear/sideways),
      Trade-PnL je Regime. entry.regimes, Chips "Bull/Bär/Seitwärts" (nur Info, kein Filter).
Technik: EINE gemeinsame Trade-Sammlung (collect_trades_list) versorgt Konstanz + MC + Regime;
Konstanz nutzt jetzt chunk_pnls_from_trades. 39 Unit-Tests grün, E2E mit allen 7 Checks verifiziert.
WICHTIG-Hinweis: optimizer.py wurde einmal durch Edit-Kollision beschädigt (duplizierter Block am
Dateiende) -> bei künftigen Edits an _finalize_top5 Syntax mit ast.parse prüfen.
- P2: Seed für Random Search (reproduzierbare Läufe)
- P2: Datenlücken-Check bei sehr langen Zeiträumen (10-15 Jahre)
