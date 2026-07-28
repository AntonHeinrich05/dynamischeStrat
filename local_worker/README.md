# Lokaler Worker – Backtests, Optimizer & Strategie-Discovery auf deinem PC

Der Worker führt Backtests, Parameter-Optimierungen und Strategie-Discovery
mit **exakt demselben Code wie der Server** aus – nur eben auf deinem Rechner.
Historische Kerzendaten werden lokal gespeichert und inkrementell aktualisiert,
dadurch entfällt der Daten-Download bei jedem Lauf fast komplett.

## Einrichtung (einmalig)

1. **Python 3.10+ installieren** (https://python.org – bei der Installation
   "Add to PATH" anhaken).
2. Dieses Zip in einen beliebigen Ordner entpacken.
3. Abhängigkeiten installieren:
   ```
   pip install -r requirements.txt
   ```
4. Worker-Token aus der Website kopieren:
   *Backtester oder Optimizer öffnen → Ausführung „Lokal" → ⚙ Verwalten → Einrichtung.*
5. Worker starten:
   ```
   python worker.py --server https://DEINE-WEBSITE --token DEIN_TOKEN
   ```
   (Windows: alternativ `start_worker.bat` doppelklicken, beim ersten Mal
   Server/Token eintragen.)

Server-URL und Token werden in `worker_config.json` gespeichert – ab dann
reicht `python worker.py`.

## Optionen

| Option | Bedeutung |
|---|---|
| `--server URL` | URL der Website |
| `--token TOKEN` | Worker-Token (Website → Lokale Ausführung → Verwalten) |
| `--data-dir PFAD` | Ordner für lokale Kerzendaten (Standard: `~/KryptoScannerDaten`) |
| `--name NAME` | Anzeigename dieses Rechners in der Website |

CPU-Kerne, RAM-Limit, parallele Jobs, Daten-Ordner und Auto-Update werden
zentral **in der Website** eingestellt (Lokale Ausführung → Verwalten) und
automatisch an den Worker übertragen.

## Wie es funktioniert

- Der Worker verbindet sich **ausgehend** zur Website (Polling) – keine
  Portfreigaben oder Router-Einstellungen nötig.
- Backtests/Optimierungen startest du ganz normal in der Website und wählst
  dort „Lokal" als Ausführung. Fortschritt, Abbruch und Ergebnisse erscheinen
  wie gewohnt in der Website.
- Kerzendaten kannst du vorab über die Daten-Verwaltung herunterladen –
  einmal geladen, laufen Tests deutlich schneller (nur die neuesten Minuten
  werden nachgeladen).
- GPU-Beschleunigung ist als Phase 2 vorgesehen; der Worker läuft vollständig
  ohne GPU.

## GPU-Beschleunigung (optional)

Mit einer NVIDIA-GPU kann der Worker die vektorisierbaren Indikator-Berechnungen
(SMA, Bollinger-Bänder, Stochastik) auf der GPU ausführen:

1. CuPy installieren (passend zur CUDA-Version):
   ```
   pip install cupy-cuda12x    # CUDA 12
   pip install cupy-cuda11x    # CUDA 11
   ```
2. In der Website aktivieren: *Lokale Ausführung → Verwalten → "GPU nutzen"*.

Die GPU wird automatisch erkannt; ohne GPU/CuPy (oder bei Fehlern) läuft
automatisch der identische CPU-Pfad – Ergebnisse sind in beiden Fällen gleich.

**Realistisch beschleunigt werden:** Indikator-Vorberechnung bei großen
Zeiträumen/vielen Coins (Discovery, Optimierung, Walk-Forward). Die
ereignisbasierte Trade-Simulation selbst und rekursive Indikatoren (EMA, RSI,
MACD) bleiben CPU-gebunden – dort bringt Multi-Core (CPU-Kerne-Einstellung)
deutlich mehr als eine GPU.
