# Verifikations-Report: Strategie- & Backtest-Konsistenz

Datum: 2026-06 · Datenbasis: BTCUSDT, 14 Tage, echte Binance-1m-Kerzen (20.160 Kerzen)
Rohdaten: `/app/test_reports/verify_consistency.json` · Skript: `/app/backend/tests/verify_consistency.py`

---

## 1. Fast-Path vs. Legacy-Pfad (alle Built-in-Strategien)

| Strategie | Fast-Path | Trades identisch? | PnL-Differenz | Speedup |
|---|---|---|---|---|
| rsi_only | ✅ | ✅ 261 = 261 | 0.00 | **202x** |
| macd_rsi_momentum | ✅ (bestand) | ⚠️ 140 vs 141 | 6.25 | **682x** |
| scalping_4_rules | ✅ (bestand) | ✅ | 0.00 | – |
| **bollinger_reversion** (NEU) | ✅ | ✅ 41 = 41 | 0.00 | **370x** |
| **bollinger_squeeze** (NEU) | ✅ | ✅ 122 = 122 | 0.00 | **1231x** |
| **ema_pullback_scalping** (NEU) | ✅ | ✅ 190 = 190 | 0.59 | **129x** |
| stoch_reversal | ✅ | ✅ 33 = 33 | 0.00 | **6050x** |
| vwap_reversion | ✅ | ✅ 69 = 69 | 0.00 | **897x** |
| ict_liquidity_sweep | ❌ bewusst Legacy | – | – | – |

- **Neu migriert:** bollinger_reversion, bollinger_squeeze, ema_pullback_scalping (inkl. Pre-Signal-Unterstützung).
- **ict_liquidity_sweep** bleibt bewusst auf dem Legacy-Pfad: Die ICT-Logik (Order Blocks, FVG, Sweep-Sequenzen) ist stark pfadabhängig und ließe sich nicht garantiert identisch vektorisieren. Der automatische Legacy-Fallback greift (Anforderung 2 erfüllt).
- **macd_rsi_momentum** (bereits vor diesem Durchgang migriert): 1 Trade Differenz von 141 (~0,7%). Ursache: EMA/MACD sind pfadabhängig – Legacy rechnet über ein 260-Kerzen-Fenster, Fast-Path über die volle Historie. An exakt einer Kerze lag der MACD-Wert um Bruchteile anders. Kein Logik-Fehler; dokumentierte Toleranz.
- **RAM Fast-Path:** FastSeries braucht ~8 Bytes/Kerze pro Indikator-Serie (numpy). Bei 360 Tagen 1m ≈ 4 MB pro Serie, wird nach jedem Symbol freigegeben. **Fast-Path ist kein RAM-Problem** – der Kerzen-Cache (dicts, ~500 Bytes/Kerze) ist der große Posten.

## 2. Hebel / Liquidation

**Vorher:** Keine Liquidationslogik. Hebel wirkte nur auf die Positionsgröße → 100x war exakt 10x-PnL von 10x. Die Beobachtung des Users war korrekt.

**Jetzt implementiert (Backtester UND Paper/Live-Monitor):**
- Liquidationspreis (Isolated Margin): `Entry × (1 ∓ (1/Hebel − MMR))`, Maintenance-Margin-Rate Default 0,5% (einstellbar `maintenance_margin_rate`).
- Kerze berührt Liquidationspreis vor dem SL → Position wird liquidiert, Verlust = eingesetzte Marge.
- Verlust ist generell auf die Marge gedeckelt (Isolated Margin).
- Backtest-Ergebnis zeigt Liquidationen pro Strategie (Spalte „Liq." im Ranking, Feld `liquidated` im CSV-Export).

**Messung (rsi_only, 14 Tage BTC):**
| Hebel | PnL | Max DD | Liquidationen |
|---|---|---|---|
| 10x | −209.07 | 213.82 | 0 |
| 100x | −2079.47 (nicht −2090.70 = 10×) | 2127.01 | **2** |

Bei 100x liegt der Liquidationspreis nur 0,5% vom Entry entfernt – enge Struktur-Stops liegen oft davor, deshalb bleibt vieles ähnlich skaliert, aber Liquidationen werden jetzt korrekt erkannt und die Marge gedeckelt.

## 3. Break-Even-Modi

Alle 5 Modi getestet (rsi_only, be_trigger_crv=0.7, be_trigger_profit_pct=20):

| Modus | Trades | PnL | be_moved |
|---|---|---|---|
| off | 260 | −205.38 | 0 |
| tp1 | 261 | −209.07 | 142 |
| crv | 293 | −254.99 | 181 |
| profit_pct | 260 | −205.38 | 0 |
| smart | 260 | −204.93 | 142 |

**Befund:** Die Modi werden korrekt und unterschiedlich angewendet (crv verändert sogar die Trade-Anzahl, weil BE-Stops Trades früher schließen und neue Entries freigeben).
- **Warum oft nur 1–2 $ Unterschied?** `tp1`, `smart` und `off` unterscheiden sich nur bei Trades, die nach TP1 zurück auf Entry fallen – das sind wenige. `profit_pct` mit hohem Trigger (30% Gewinn auf die Marge) wird bei 1m-Scalps **selten erreicht** (0 von 260 Trades bei Trigger 20%!). Kein Bug – der Trigger ist für kurze Timeframes schlicht zu hoch. Empfehlung: bei 1m/5m-Scalping `profit_pct`-Trigger 5–15% oder `crv`-Modus mit 0.5–1R verwenden.

## 4. Discovery-/Custom-Strategien

**Gefundener Bug (behoben):** `CustomStrategy` ignorierte den in der Definition gespeicherten Timeframe und lief immer auf 1m (`STRATEGY_TIMEFRAME` war hart „1m"). Eine per Discovery auf 5m erstellte Strategie wurde also standardmäßig auf 1m getestet. Behoben: Timeframe kommt jetzt aus der Definition.

**Timeframe wirkt nachweislich** (Custom RSI-Strategie, identische Daten):
| TF | Trades | PnL |
|---|---|---|
| 1m | 275 | −254.27 |
| 5m | 95 | −134.42 |
| 15m | 36 | −42.13 |

Fast-Path und Legacy sind für Custom-Strategien auf allen Timeframes **100% identisch** (Trades & PnL, Differenz 0.00).

**Regeln bearbeiten:** Discovery-Regeln sind im Strategie-Builder editierbar (Stift-Symbol). Im Backtester-⚙-Panel gibt es jetzt einen Hinweis darauf; TP/SL/BE/Timeframe/Gewinnsicherung sind pro Backtest überschreibbar.

## 5. Mehrere gleichzeitige Trades pro Coin?

- **Backtester:** max. **1 offener Trade pro (Strategie, Coin)** – neue Signale werden ignoriert solange ein Trade offen ist (`simulate_pair`, `open_t is None`-Check).
- **Paper/Live:** max. **1 offener Trade pro Coin insgesamt** – auch über Strategien hinweg (`bitunix_trade.on_signal`: `find_one({"symbol":…, "status":"open"})`).
- **Dokumentierter Unterschied:** Testet man mehrere Strategien gleichzeitig auf demselben Coin, kann der Backtester pro Strategie je 1 Trade offen haben, Live/Paper insgesamt nur 1. Für 1 Strategie pro Coin (Normalfall) sind beide identisch.

## 6. Backtester vs. Paper-Trading (Logik-Vergleich)

Gleiche Formeln in beiden Pfaden verifiziert (Code-Referenzen: `backtester.simulate_pair` ↔ `bitunix_trade._manage_trade`):
- Entry-Regeln: identische `check_signal`/Provider-Logik, gleiche `require_all_rules`- und Pre-Signal-Filter.
- SL-Berechnung: identisch (Struktur/ATR/Fest%, min_risk_percent-Floor).
- TP1-Teilverkauf, TP-Full, Gebühren pro Fill: identische Formeln.
- Break-Even (alle 5 Modi), Gewinnsicherung, ATR-Trailing: identische Trigger und Preise.
- **NEU beidseitig:** Liquidationsprüfung + Margen-Deckel, TP-Modi (crv/fixed_pct/structure).
- Bekannter, prinzipbedingter Unterschied: Der Backtester arbeitet mit abgeschlossenen OHLC-Kerzen (konservativ: SL vor TP bei Berührung in derselben Kerze), Paper/Live mit Live-Tick-Preisen. Ergebnisse sind daher „nahezu identisch", nicht bitgenau.

## 7. RAM-Bewertung: Was braucht wie viel?

| Komponente | Verbrauch | Bewertung |
|---|---|---|
| **Kerzen-Cache (dicts)** | ~500 Bytes/Kerze → 360 Tage 1m = ~260 MB pro Coin | **Größter Posten.** LRU-Limit 500k Kerzen, Rest geht auf Disk (/tmp) |
| Backtest-Export-Puffer | Kerzen (max 400k) + Trades (max 100k) des letzten Laufs | zweitgrößter Posten, per „Cache leeren" freigebbar |
| Fast-Path (FastSeries) | ~8 Bytes/Kerze/Serie (numpy), pro Symbol freigegeben | **gering – Fast-Path ist NICHT das RAM-Problem** |
| Python-Grundlast | ~150–250 MB | fix |

**360 Tage · 5m · alle 10 Coins · 1 Strategie – geht das?** Ja: Symbole werden sequenziell verarbeitet (nach jedem Symbol `del history` + `gc.collect()`). Peak ≈ Grundlast + 1 Symbol-Historie (~260 MB) + Cache-Anteil. Auf Render-Free (512 MB) knapp → vor dem Lauf „Cache leeren" drücken und `CANDLE_CACHE_MAX_CANDLES` klein halten (z.B. 200000); der Disk-Cache übernimmt. Auf 1-GB-Instanzen problemlos.

**Neue Werkzeuge:** RAM-Anzeige + „Cache leeren"-Button + Fast-Path-Schalter im Backtester, `GET /api/system/ram`, `POST /api/system/cache/clear`.

## 8. Coin-Laden optimiert

Fehlende Kerzenbereiche werden jetzt in 3 parallelen Teilbereichen von Binance geladen (~3x schneller bei großen Zeiträumen), ohne Rate-Limits zu reißen. Wiederholte Läufe nutzen weiter den Hybrid-Cache (nur der fehlende „Tail" wird nachgeladen).

## 9. Optimierungs-Geister-Job (Bug behoben)

- Ursache: Der Optimizer-Dialog prüfte beim Öffnen nicht, ob eine Optimierung läuft → Fortschritt/Abbrechen unsichtbar, Backend blockierte mit „Es läuft bereits eine Optimierung".
- Fix 1: Dialog zeigt laufende Jobs nach dem Öffnen wieder an (Fortschritt + Abbrechen).
- Fix 2: **Notfall-Reset-Buttons** (Optimizer & Backtester) → `POST /api/optimizer/reset` / `POST /api/backtest/reset` geben hängende Jobs sofort frei.
- Fix 3: Ghost-Job-Schutz: Stirbt ein Job-Task unerwartet, wird der Job automatisch als Fehler markiert statt ewig „running" zu bleiben.
