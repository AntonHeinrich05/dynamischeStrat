# Bitunix Live-Trading Fix (Codes 30016 & 30027) + TP1/Break-Even auf Exchange

Änderungen in **`backend/services/bitunix_trade.py`**, **`backend/services/telegram_bot.py`** und **`backend/server.py`**.

## Was war kaputt?

Aus deinen Telegram-Screenshots:

1. **`code 30016: The amount should be larger than 0.0001 BTC`**
   → Ordermenge kam als **`0`** bei Bitunix an.
2. **`code 30027: TP price must be greater than mark price: 77.29`**
   → Take-Profit lag auf oder unter dem Mark-Preis.

## Root Cause

`load_trading_pairs()` hat die Felder `basePrecision` / `quotePrecision`
von Bitunix als **Step-Size** interpretiert. Bitunix liefert dort aber
**Dezimalstellen** (Integer: `3`, `4`, `5` …).

Folge: `_round_step(0.0154, 3.0)` → `(0.0154 / 3.0).floor() * 3.0 = 0`.
Die Ordermenge wurde also auf `0` abgerundet → **30016**.
Für Preise passierte dasselbe → TP landete auf `0` oder gerundet unter dem
Mark-Price → **30027**.

Zusätzlich: Bei sehr kleinem Risk (z. B. `0.07 %`, siehe SOLUSDT / XRPUSDT im
Screenshot) rutscht der Mark-Price zwischen Signal und Order-Ausführung
schon über den TP → ebenfalls **30027**.

## Fixes

| # | Was | Wo |
|---|-----|----|
| 1 | Neue Hilfsfunktion **`_precision_to_step()`** – erkennt automatisch, ob Bitunix Dezimalstellen (3 → 0.001) oder eine echte Step-Size (0.001) liefert. | `bitunix_trade.py` |
| 2 | `load_trading_pairs()` benutzt jetzt `_precision_to_step()` für `qty_step` und `price_tick`. | `load_trading_pairs()` |
| 3 | `_round_step()` respektiert die Step-Präzision explizit (kein `.normalize()` → kein "1.11" statt "1.1100"), rounding-mode ist parametrisierbar. | `_round_step()` |
| 4 | `_fmt_qty()` **erzwingt `min_qty`** – wenn die berechnete Menge kleiner ist, wird auf das Bitunix-Minimum hochgesetzt (kein 30016 mehr durch Rounding-to-Zero). | `_fmt_qty()` |
| 5 | `_fmt_price()` bekommt **direction-aware rounding**: LONG-TP rundet UP, LONG-SL rundet DOWN, SHORT umgekehrt. So kann Tick-Rounding den TP nie unter den Mark-Price schieben. | `_fmt_price()` |
| 6 | `place_order()` ruft `_fmt_price()` mit passender Richtung auf. | `place_order()` |
| 7 | Neue Methode **`get_mark_price()`** – holt live den Mark-Price über `GET /api/v1/futures/market/tickers`. | `BitunixTradeClient` |
| 8 | `AutoTradeManager._levels()` erzwingt einen **Mindest-Risk** (`min_risk_percent`, default **0.25 %**). So kann Risk nie unter dem sein, was Bitunix Slippage sicher erlaubt. | `_levels()` |
| 9 | `AutoTradeManager.on_signal()` re-aligned TP/SL kurz vor dem Order-Placement gegen den **aktuellen Mark-Price** (`min_tp_distance_percent`, default **0.15 %**). Wenn der Markt schon durch TP gelaufen ist, wird TP nach vorne geschoben statt Order abzulehnen. | `on_signal()` |
|10 | Kapital-Guard: wenn `qty < min_qty` und Kapital nicht reicht, wird der Trade **sauber übersprungen** mit Telegram-Meldung (statt Bitunix mit 30016 abzulehnen). | `on_signal()` |

## Neue Config-Keys (mit Defaults, alte Configs bleiben kompatibel)

```python
DEFAULT_COIN_CFG = {
    ...
    "min_tp_distance_percent": 0.15,  # min Abstand TP/SL vom Mark-Price (%)
    "min_risk_percent":       0.25,   # Floor fuer Risk (% vom Entry)
}
```

Kannst du pro Coin in deiner Trading-Config überschreiben, falls du für
bestimmte Symbole engere / weitere Werte willst.

## Verifikation

```
python -c "from services.bitunix_trade import _round_step, _precision_to_step
from decimal import ROUND_DOWN
assert _precision_to_step(3) == 0.001
assert _round_step(0.0154, 0.001, ROUND_DOWN) == '0.015'  # war vorher '0'
print('OK')"
```

## Deployment

Kein Datenbank-Migrationsschritt nötig. Einfach:

```
git pull        # oder Dateien aus dem ZIP übernehmen
sudo supervisorctl restart backend
```

Die nächsten Live-Orders sollten die Fehler 30016 / 30027 nicht mehr sehen.
Sollte doch mal 30027 auftauchen, in der Coin-Config `min_tp_distance_percent`
z. B. auf `0.25` hochziehen.

---

## Update Runde 2 – TP1 & Break-Even sind jetzt auch auf Bitunix aktiv

Vorher wurden TP1 (Partial 50 %) und der SL→Break-Even Move **nur lokal**
über den Monitor gemacht. Bitunix hatte davon nichts mitbekommen – wenn der
Backend-Prozess neu startet, lief die Position komplett unabgesichert bis
TP-Full / Original-SL.

**Fixes:**

1. Neue Client-Methoden **`place_position_tp_sl(...)`** und
   **`modify_position_tp_sl(...)`** hitten
   `POST /api/v1/futures/tpsl/place_order` bzw.
   `POST /api/v1/futures/tpsl/modify_position_tp_sl_order` mit korrektem
   direction-aware Rounding.
2. **`resolve_position_id(symbol, side)`** pollt `get_pending_positions`
   direkt nach dem Entry, damit wir die `positionId` haben (Bitunix gibt
   sie im place_order-Response nicht mit).
3. **`AutoTradeManager.on_signal`** platziert nach erfolgreicher Entry-
   Order sofort eine **reduce-only TP1-Partial-Order** auf Bitunix mit
   `qty = qty * tp1_close_percent / 100`. Ergebnis wird als
   `tp1_exchange_placed=True/False` gespeichert.
4. **`AutoTradeManager._manage_trade`** synchronisiert bei TP1-Hit den
   **SL auf Break-Even** über `place_position_tp_sl(sl_price=BE)`. Auch der
   ATR-Trailing-SL wird jetzt an Bitunix gepusht (`_live_move_sl`).
5. Wenn TP1 bereits auf der Exchange platziert wurde, überspringt der
   lokale Monitor den `flash_close` (kein doppeltes Schließen).
6. `bitunix_position_id` und `tp1_exchange_placed` sind neu im
   `auto_trades`-Dokument (backwards-compatible: alte Trades ohne diese
   Felder fallen auf das alte Verhalten zurück).

**Telegram cosmetic fix:** Das hardcoded `TP1 (40%)` wurde durch
`TP1 ({tp1_close_percent}%)` ersetzt. `server.py` reichert das Signal-Dict
vor dem Senden mit der aus der Coin-Config gelesenen `tp1_close_percent`
an, sodass die Anzeige immer zum tatsächlich ausgeführten Partial passt.

**Nichts kaputt bei Paper-Mode:** Alle Exchange-Calls sind hinter
`mode == "live"` gated, Paper-Trading verhält sich exakt wie vorher.

### Fallback-Verhalten (Robustheit)

- Wenn `resolve_position_id` scheitert → TP1-Partial auf Exchange wird
  übersprungen, `tp1_exchange_placed=False` → der lokale Monitor
  übernimmt via `flash_close` wie bisher.
- Wenn `place_position_tp_sl` (BE-Move) fehlschlägt → SL bleibt auf
  Exchange auf dem alten Level, aber Local-DB SL ist BE. Der Monitor
  triggert BE-Close, sobald der Preis den lokalen BE-SL touched.

Keine Situation ist "schlimmer" als vorher – nur besser.

---

## Update Runde 3 – Zeitzone auf Europa / Deutschland

Vorher zeigte der Chart Zeiten in **Englisch (`en-US`) mit UTC / Browser-TZ**,
Header/SignalPanel/PerformanceAnalytics respektierten die deutsche Locale
formatmäßig, aber nicht die Zeitzone (falls jemand auf einem US-Server /
US-Browser guckt, kamen US-Zeiten raus).

**Fixes:**

- `frontend/src/components/MainChart.js`
  - `localization.locale` von `en-US` → `de-DE`.
  - Eigene `tickMarkFormatter` und `timeFormatter`, die
    `Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin' })` benutzen.
    Damit steht auf der X-Achse und im Crosshair-Tooltip **immer**
    deutsche Uhrzeit (Sommer-/Winterzeit automatisch).
- `Header.js`, `SignalPanel.js`, `PerformanceAnalytics.js` bekommen alle
  ein explizites `timeZone: 'Europe/Berlin'` in ihren `toLocaleTimeString`-
  Aufrufen. Kein "Zufall" mehr durch Browser-Zeitzone.

Backend-seitig speichern wir Timestamps weiterhin als UTC-ISO in Mongo
(korrekt, TZ-neutral) – die Umrechnung passiert nur beim Anzeigen im
Frontend. So bleiben Reports & Auswertungen konsistent.
