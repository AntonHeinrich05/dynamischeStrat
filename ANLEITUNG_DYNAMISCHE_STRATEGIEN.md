# Bedienung: Dynamische Strategien & lokaler Worker

## 1. Der richtige Ablauf (Kurzfassung)

1. **Daten einmal lokal herunterladen** – Optimizer → *Lokale Ausführung* → *Verwalten*
   → Coin + Zeitraum wählen → *Herunterladen*.
   Der Download läuft in 360-Tage-Etappen und wird nach jeder Etappe gespeichert.
   Ein Abbruch verliert also nichts – einfach erneut starten, es geht dort weiter.
2. **Marktphase prüfen** – Optimizer → *Dynamische Strategien* → Karte **„Aktuelle Marktphase"**.
   Coin/Timeframe/Zeitraum wählen → *Marktphase bestimmen*. Zeigt sofort:
   in welcher Phase der Markt gerade ist, wie sicher die Einordnung ist,
   wie oft im Zeitraum umgeschaltet wurde und die letzten Phasenwechsel.
3. **Dynamische Optimierung starten** – Optimizer → Modus *Dynamische Strategie*.
4. **Ergebnis speichern** – im Ergebnis auf *Dynamische Strategie speichern*.
   Erst danach wird sie live überwacht und im Panel angezeigt.

## 2. Die zwei Betriebsarten der dynamischen Optimierung

| Option | Was passiert | Wann nutzen |
|---|---|---|
| *(nichts angehakt)* | Regeln bleiben gleich, **nur Trade-Parameter** (TP/SL, Hebel, BE …) werden pro Marktphase optimiert | schnell, gut für den Einstieg |
| **Regel-Varianten pro Regime** | wie oben + **eine** zusätzliche Regel je Phase | mittlerer Aufwand |
| **Eigene Strategie pro Marktphase suchen** | pro Phase wird eine **komplett eigene Strategie** gesucht (eigene Regeln **und** eigene Trade-Parameter) | dauert am längsten, liefert echte Sub-Strategien |

Bei „Eigene Strategie pro Marktphase" musst du unten die **Indikatoren anhaken**,
aus denen die Regeln gebaut werden dürfen. Je mehr Indikatoren, desto größer der
Suchraum (und desto länger die Laufzeit).

## 3. Walk-Forward – pro Marktphase

Jede Sub-Strategie muss zwei Prüfungen bestehen:

1. **Walk-Forward innerhalb ihrer eigenen Phase**
   Die Abschnitte dieser Marktphase werden in Training (75 %) und Validierung (25 %)
   geteilt. Nur Regel-Kombinationen und Parameter, die auf dem *unbekannten* Teil
   derselben Phase noch profitabel sind, werden übernommen.
   Besteht keine Kombination → für diese Phase bleibt die Basis-Strategie aktiv
   (steht so in der Ergebnistabelle).
2. **Gesamtvergleich gegen die statische Benchmark**
   Am Ende wird die komplette dynamische Strategie gegen die beste *statische*
   Konfiguration auf dem ausgelassenen Testzeitraum verglichen. Nur wenn sie klar
   besser ist, wird sie empfohlen.

Das ist der Grund, warum manchmal „Statische Strategie bevorzugen" herauskommt:
Die dynamische Variante war auf unbekannten Daten nicht nachweisbar besser.
Das ist ein ehrliches Ergebnis, kein Fehler.

## 4. Warum finde ich keine guten Strategien?

Typische Ursachen, in dieser Reihenfolge prüfen:

- **Min. Trades zu niedrig.** Bei 10 Mindest-Trades über 365 Tage ist fast jedes
  Ergebnis Zufall. Faustregel: mindestens **30–50 Trades pro Marktphase**,
  also `Min. Trades` eher auf 60–100 stellen, wenn der Zeitraum lang ist.
- **Zeitraum zu kurz.** Für 4–5 Marktphasen brauchst du ≥ 360 Tage, besser 1080+.
- **Zu wenige Indikatoren freigegeben.** Mit 3 Indikatoren gibt es kaum Kombinationen.
- **Zu viele Regeln erlaubt.** `Max. Regeln je Sub-Strategie` > 4 führt fast immer
  zu Overfitting; der Walk-Forward wirft die Ergebnisse dann wieder raus.
- **Timeframe.** 5m über 5400 Tage sind 1,5 Mio. Kerzen – das geht, aber die
  Marktbedingungen von 2011 haben mit heute wenig zu tun. 1080–1800 Tage sind
  oft aussagekräftiger als 5400.

## 5. Lokaler Worker

### Aktualisieren (wichtig nach diesem Update)
Der Worker muss **Version 1.4.0** haben. Im Panel steht die Version; ist sie älter,
erscheint eine Warnung.

1. Worker-Fenster schließen (Strg+C)
2. *Worker herunterladen (Zip)* im Panel
3. Zip **über den alten Ordner** entpacken (`worker_config.json` bleibt erhalten)
4. `python worker.py` neu starten

Deine heruntergeladenen Kerzen bleiben erhalten. Alte `.pkl.gz`-Dateien werden beim
ersten Zugriff automatisch in das neue, viel schnellere `.npy`-Format umgewandelt.

### Was sich technisch geändert hat
- Kerzen liegen jetzt **spaltenbasiert** (numpy) statt als Listen von Dicts:
  5400 Tage 1-Minuten-Kerzen brauchen **373 MB statt ~3,3 GB** RAM.
  Genau das war die Ursache für die Abstürze beim Laden gespeicherter Kerzen.
- Laden von Platte: **~0,1 s statt Minuten**; Aggregation auf 5m: ~0,2 s.
- Ein abgestürzter Job meldet jetzt **immer** einen Fehler zurück, statt die
  Website bis zum Timeout hängen zu lassen.
- Ergebnis-Upload wird bis zu 5-mal wiederholt.
- Timeouts sind großzügiger (Heartbeat 90 s, Job-Stillstand 15 min).

### RAM-Limit
Im Worker-Panel unter *Einstellungen*. 1 MB entspricht jetzt ca. 20.000 Kerzen
(vorher 2.000). Mit 4096 MB passen ~80 Mio. Kerzen in den Speicher – das reicht
für mehrere Coins über 5400 Tage.

### GPU
Wird nur genutzt, wenn eine NVIDIA-GPU **und** CuPy installiert sind
(`pip install cupy-cuda12x`). Ohne CuPy zeigt das Panel „GPU: –"; die Rechnung
läuft dann über alle CPU-Kerne (Multi-Core-Anzeige im Panel).
