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

## Test-Status
- Testing-Agent Iteration 12: Backend 23/23 bestanden, Frontend strukturell verifiziert (alle data-testids, Modus-Umschaltung, Panels)
- Bestehende Modi (params/discovery/combo), Backtester, Apply-Flows: Regression bestanden

## Bekannte Minor-Punkte (vorbestehend, nicht blockierend)
- Console: 401/Failed-to-fetch vor Admin-Login (fetchNotif/fetchSession)
- React-Warnung: <span> in <option> in einem Select

## Backlog / Nächste Schritte
- P1: Automatische Regime-Umschaltung im Live-Betrieb (Scheduler statt Button), mit Wechsel-Historie/Protokoll
- P1: Dynamik-Modus auch über lokalen Worker ausführbar machen (execution=local, numpy im Worker-Requirements prüfen)
- P2: Pro Regime optional andere Regeln/Indikatoren testen (nicht nur Trade-Parameter)
- P2: Lern-Datenbank: Ergebnisse aus WF/MC/Konstanz je Marktphase sammeln und für spätere Suchen priorisieren
- P2: Regime-Auto-Anzahl zusätzlich mit Gap-Statistik validieren
