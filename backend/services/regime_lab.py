"""Regime-Lab: Regime-Analysen erstellen, speichern und für die
regime-gezielte Strategie-Suche wiederverwenden.

Kernidee (siehe Anforderungen):
- Für eine Konfiguration (Coins + Timeframe + Zeitraum + Regime-Einstellungen)
  werden Marktphasen gesucht und gespeichert – kombiniert über alle Coins UND
  je Coin einzeln, damit man vergleichen kann, ob Coins ähnliche Phasen haben.
- Die Analyse speichert je Coin einen komprimierten Kursverlauf + die
  Regime-Abschnitte, damit das Frontend die Phasen direkt am Chart anzeigen kann.
- Optionaler Holdout (train_pct < 100): Das Regime-Modell wird NUR auf dem
  Trainingsteil geclustert; der hintere Teil bleibt unangetastet für den
  finalen Walk-Forward-Test der zusammengestellten dynamischen Strategie.
- Klassifikation ist rein rückblickend (services.regime) -> kein Lookahead.
"""
import bisect
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

from services import regime as rg
from services.backtester import JobCancelled

logger = logging.getLogger(__name__)

JOBS: Dict[str, Dict] = {}

CHART_MAX_POINTS = 1200
MAX_ANALYSES = 40


def create_job(kind: str, params: Dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "kind": kind, "status": "running", "progress": 0,
                    "phase": "Startet", "params": params, "cancel": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "result": None, "error": None}
    if len(JOBS) > 10:
        for k in list(JOBS.keys())[:-10]:
            JOBS.pop(k, None)
    return job_id


def running_job() -> Optional[Dict]:
    for j in JOBS.values():
        if j.get("status") == "running":
            return j
    return None


async def fetch_histories(symbols: List[str], days: int, timeframe: str,
                          job: Dict = None, end_ts: Dict[str, int] = None,
                          progress_span=(0, 10)) -> Dict[str, List[Dict]]:
    """Kerzen laden + auf den Timeframe aggregieren. Mit end_ts (aus einer
    gespeicherten Analyse) werden die Daten exakt auf den Analyse-Zeitraum
    geschnitten, damit spätere Läufe reproduzierbar bleiben."""
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles
    histories: Dict[str, List[Dict]] = {}
    p0, p1 = progress_span
    async with aiohttp.ClientSession() as session:
        for i, sym in enumerate(symbols):
            if job and job.get("cancel"):
                raise JobCancelled()
            if job:
                job["phase"] = f"Lade Daten: {sym}"
                job["progress"] = p0 + round(i / max(len(symbols), 1) * (p1 - p0))
            raw = await fetch_history(session, sym, days, job=job)
            candles = aggregate_candles(raw, timeframe)
            del raw
            if end_ts and end_ts.get(sym):
                candles = [c for c in candles if c["timestamp"] <= end_ts[sym]]
            if len(candles) > 100:
                histories[sym] = candles
    return histories


def _downsample(candles: List[Dict], max_pts: int = CHART_MAX_POINTS) -> List[List]:
    step = max(len(candles) // max_pts, 1)
    pts = [[int(c["timestamp"]), float(c["close"])] for c in candles[::step]]
    last = candles[-1]
    if pts and pts[-1][0] != int(last["timestamp"]):
        pts.append([int(last["timestamp"]), float(last["close"])])
    return pts


def _segments_payload(candles: List[Dict], labels: List) -> List[Dict]:
    out = []
    for (s, e, rid) in rg.segments_from_labels(labels):
        out.append({"regime": int(rid),
                    "from_ts": int(candles[s]["timestamp"]),
                    "to_ts": int(candles[min(e, len(candles) - 1)]["timestamp"]),
                    "bars": int(e - s)})
    return out


def _regime_usage(segments_by_sym: Dict[str, List[Dict]], timeframe: str) -> Dict:
    """Wie viele Bars/Tage entfallen je Regime auf die Analyse? (Plausibilitäts-Check)"""
    bpd = rg.bars_per_day(timeframe)
    usage: Dict[int, Dict] = {}
    for segs in segments_by_sym.values():
        for s in segs:
            u = usage.setdefault(s["regime"], {"bars": 0, "segments": 0})
            u["bars"] += s["bars"]
            u["segments"] += 1
    return {str(k): {"bars": v["bars"], "segments": v["segments"],
                     "days": round(v["bars"] / max(bpd, 1e-9), 1)}
            for k, v in usage.items()}


def _coin_similarity(histories: Dict[str, List[Dict]],
                     labels_map: Dict[str, List]) -> List[Dict]:
    """Anteil der Zeit, in der zwei Coins (unter dem kombinierten Modell) im
    selben Regime sind – hilft beim Finden von Coins mit ähnlichen Phasen."""
    ts_maps = {}
    for sym, candles in histories.items():
        labels = labels_map.get(sym) or []
        ts_maps[sym] = {int(candles[i]["timestamp"]): labels[i]
                        for i in range(len(labels)) if labels[i] is not None}
    syms = sorted(ts_maps.keys())
    out = []
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = ts_maps[syms[i]], ts_maps[syms[j]]
            common = a.keys() & b.keys()
            if not common:
                continue
            same = sum(1 for t in common if a[t] == b[t])
            out.append({"a": syms[i], "b": syms[j],
                        "agreement_pct": round(same / len(common) * 100, 1),
                        "bars": len(common)})
    out.sort(key=lambda x: -x["agreement_pct"])
    return out


async def run_analysis(job_id: str, body: Dict, db):
    """Regime-Analyse-Job: Modelle clustern (kombiniert + je Coin), Abschnitte
    berechnen und alles als wiederverwendbare Analyse speichern."""
    job = JOBS[job_id]
    try:
        symbols = body.get("symbols") or []
        timeframe = body.get("timeframe") or "5m"
        days = int(min(max(int(body.get("days") or 180), 7), 5500))
        scope = body.get("scope") or "both"
        max_regimes = int(min(max(int(body.get("max_regimes") or 5), 2), 10))
        lookback_days = float(min(max(float(body.get("lookback_days") or 3), 0.5), 60))
        min_share = float(min(max(float(body.get("min_share_pct") or 5), 1), 30))
        conf_min = float(min(max(float(body.get("confidence_min") or 70), 50), 95)) / 100.0
        min_hold_days = float(min(max(float(body.get("min_hold_days") or 2), 0.25), 60))
        train_pct = float(min(max(float(body.get("train_pct") or 100), 50), 100))

        histories = await fetch_histories(symbols, days, timeframe, job)
        if not histories:
            raise RuntimeError("Zu wenig Daten für diesen Timeframe/Zeitraum")

        def stop():
            return bool(job.get("cancel"))

        bounds = {}
        train_hist = {}
        for sym, candles in histories.items():
            cut = int(len(candles) * train_pct / 100.0)
            cut = min(max(cut, 100), len(candles))
            train_hist[sym] = candles[:cut]
            bounds[sym] = {"start_ts": int(candles[0]["timestamp"]),
                           "end_ts": int(candles[-1]["timestamp"]),
                           "train_end_ts": (int(candles[cut - 1]["timestamp"])
                                            if cut < len(candles) else None),
                           "bars": len(candles)}

        combined = None
        if scope in ("both", "combined"):
            if stop():
                raise JobCancelled()
            job["phase"] = "Kombiniertes Regime-Modell clustern (alle Coins)"
            job["progress"] = 20
            model = rg.detect_regimes(train_hist, timeframe, max_regimes,
                                      lookback_days, min_share)
            if model:
                labels_map, per_symbol = {}, {}
                for sym, candles in histories.items():
                    labels = rg.classify_series(model, candles, timeframe,
                                                conf_min, min_hold_days)
                    labels_map[sym] = labels
                    per_symbol[sym] = {"segments": _segments_payload(candles, labels)}
                segs_by_sym = {s: v["segments"] for s, v in per_symbol.items()}
                combined = {"model": model, "per_symbol": per_symbol,
                            "usage": _regime_usage(segs_by_sym, timeframe),
                            "coin_similarity": _coin_similarity(histories, labels_map)}
        per_coin = {}
        if scope in ("both", "per_coin"):
            for i, (sym, candles) in enumerate(histories.items()):
                if stop():
                    raise JobCancelled()
                job["phase"] = f"Regime-Modell je Coin: {sym}"
                job["progress"] = 40 + round(i / max(len(histories), 1) * 50)
                model_s = rg.detect_regimes({sym: train_hist[sym]}, timeframe,
                                            max_regimes, lookback_days, min_share)
                if not model_s:
                    per_coin[sym] = {"error": "Zu wenig Daten für dieses Coin-Modell"}
                    continue
                labels = rg.classify_series(model_s, candles, timeframe,
                                            conf_min, min_hold_days)
                segs = _segments_payload(candles, labels)
                per_coin[sym] = {"model": model_s, "segments": segs,
                                 "usage": _regime_usage({sym: segs}, timeframe)}

        if not combined and not per_coin:
            raise RuntimeError("Regime konnten nicht bestimmt werden – Zeitraum erhöhen")

        job["phase"] = "Analyse speichern"
        job["progress"] = 95
        aid = f"ra_{uuid.uuid4().hex[:8]}"
        doc = {"id": aid,
               "name": body.get("name") or f"Regime-Analyse {timeframe} · {days}d",
               "symbols": list(histories.keys()), "timeframe": timeframe,
               "days": days, "scope": scope,
               "settings": {"max_regimes": max_regimes, "lookback_days": lookback_days,
                            "min_share_pct": min_share,
                            "confidence_min": round(conf_min * 100, 0),
                            "min_hold_days": min_hold_days, "train_pct": train_pct},
               "bounds": bounds,
               "chart": {sym: _downsample(c) for sym, c in histories.items()},
               "combined": combined, "per_coin": per_coin,
               "kept": {}, "assignments": {}, "walkforward": {},
               "created_at": datetime.now(timezone.utc).isoformat()}
        await db.regime_analyses.replace_one({"id": aid}, doc, upsert=True)
        n = await db.regime_analyses.count_documents({})
        if n > MAX_ANALYSES:
            old = await db.regime_analyses.find().sort("created_at", 1) \
                .limit(n - MAX_ANALYSES).to_list(n)
            for o in old:
                await db.regime_analyses.delete_one({"id": o["id"]})
        job["result"] = {"analysis_id": aid}
        job["status"] = "done"
        job["progress"] = 100
        job["phase"] = "Fertig"
    except JobCancelled:
        job["status"] = "cancelled"
        job["phase"] = "Abgebrochen"
    except Exception as e:  # noqa: BLE001 – Job-Fehler sauber melden
        logger.exception(f"regime analysis {job_id} failed")
        job["status"] = "error"
        job["error"] = str(e)[:300]
        job["phase"] = "Fehler"


# ---------------- Wiederverwendung gespeicherter Analysen ----------------
def model_for(doc: Dict, scope: str, symbol: str = None) -> Optional[Dict]:
    if scope == "per_coin":
        return ((doc.get("per_coin") or {}).get(symbol) or {}).get("model")
    return ((doc.get("combined") or {}).get("model"))


def scope_key(scope: str, symbol: str = None) -> str:
    return f"per_coin:{symbol}" if scope == "per_coin" else "combined"


def regime_ranges(doc: Dict, scope: str, symbol: str, sym: str,
                  regime_id: int, only_train: bool = True) -> List[Dict]:
    """Gespeicherte Zeitbereiche eines Regimes für ein Symbol; optional auf den
    Trainingsteil geschnitten (der Holdout bleibt für den Walk-Forward unberührt)."""
    if scope == "per_coin":
        segs = ((doc.get("per_coin") or {}).get(symbol) or {}).get("segments") or []
    else:
        segs = (((doc.get("combined") or {}).get("per_symbol") or {})
                .get(sym) or {}).get("segments") or []
    train_end = (doc.get("bounds") or {}).get(sym, {}).get("train_end_ts")
    out = []
    for s in segs:
        if s["regime"] != regime_id:
            continue
        from_ts, to_ts = s["from_ts"], s["to_ts"]
        if only_train and train_end:
            if from_ts > train_end:
                continue
            to_ts = min(to_ts, train_end)
        out.append({"from_ts": from_ts, "to_ts": to_ts})
    return out


def segments_from_ranges(candles: List[Dict], ranges: List[Dict], regime_id: int,
                         warmup_bars: int) -> List[Dict]:
    """Zeitbereiche auf (beliebige, ggf. andere Timeframe-) Kerzen abbilden –
    inkl. Warmup-Vorlauf, damit Indikatoren korrekt anlaufen."""
    ts = [c["timestamp"] for c in candles]
    segs = []
    for r in ranges:
        s = bisect.bisect_left(ts, r["from_ts"])
        e = bisect.bisect_right(ts, r["to_ts"])
        if e - s < 10:
            continue
        w0 = max(s - warmup_bars, 0)
        segs.append({"regime": regime_id, "start_ts": candles[s]["timestamp"],
                     "candles": candles[w0:e], "n_bars": e - s})
    return segs
