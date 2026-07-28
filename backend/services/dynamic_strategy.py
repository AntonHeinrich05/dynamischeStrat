"""Dynamische Strategien: pro Marktregime eine eigene Sub-Strategie.

Design-Prinzipien (siehe Anforderungen):
- Regime-Erkennung ohne Lookahead (services.regime), Anzahl automatisch bestimmt.
- Pro Regime kann eine VOLLSTÄNDIGE eigene Strategie gesucht werden (eigene
  Regeln + eigene Trade-Parameter) – nicht nur eine Feinjustierung.
- Pro Regime wird NUR optimiert, wenn genügend Trades vorhanden sind; sonst
  greift die statische Fallback-Konfiguration.
- Eine dynamische Strategie wird IMMER gegen die beste statische Konfiguration
  (gleiches Suchbudget) auf unbekannten Testdaten verglichen. Nur wenn sie klar
  besser ist, wird sie empfohlen.
- Beim Regimewechsel im Backtest werden offene Positionen zum Umschaltzeitpunkt
  geschlossen (konservativ, transparent dokumentiert).
"""
import asyncio
import copy
import json
import logging
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from services import fast_sim, regime
from services.backtester import JobCancelled, simulate_pair

logger = logging.getLogger(__name__)

WARMUP_BARS = 300
MIN_TRADES_PER_REGIME_FACTOR = 0.5  # min_trades * Faktor je Regime
MAX_PROVIDER_CACHE = 6              # Regel-Varianten je Segment im Speicher


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def metrics_from_rows(rows: List[Dict], capital: float) -> Dict:
    """Metriken aus einer Trade-Liste – identische Definitionen wie simulate_pair
    (Winrate aus PnL, Drawdown aus chronologischer Equity-Kurve)."""
    eps = 1e-6
    rows = sorted([r for r in rows if r.get("closed")], key=lambda r: r["closed"])
    wins = sum(1 for r in rows if (r.get("pnl") or 0) > eps)
    losses = sum(1 for r in rows if (r.get("pnl") or 0) < -eps)
    breakevens = len(rows) - wins - losses
    pnl = sum(float(r.get("pnl") or 0) for r in rows)
    fees = sum(float(r.get("fees") or 0) for r in rows)
    eq = peak = dd = 0.0
    for r in rows:
        eq += float(r.get("pnl") or 0)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    decided = wins + losses
    cap = float(capital or 100)
    return {"trades": len(rows), "wins": wins, "losses": losses,
            "breakevens": breakevens,
            "win_rate": round(wins / decided * 100, 1) if decided else 0.0,
            "pnl": round(pnl, 2), "fees": round(fees, 2),
            "max_drawdown": round(dd, 2),
            "avg_pnl": round(pnl / len(rows), 3) if rows else 0.0,
            "pnl_pct": round(pnl / cap * 100, 1),
            "max_drawdown_pct": round(dd / cap * 100, 1)}


def build_segments(histories: Dict[str, List[Dict]], labels_map: Dict[str, List],
                   offset_map: Dict[str, int] = None) -> Dict[str, List[Dict]]:
    """Pro Symbol: zusammenhängende Regime-Abschnitte inkl. Warmup-Slice.
    offset_map: Label-Index 0 entspricht Kerze offset im (vollen) Kerzen-Array."""
    out = {}
    for sym, candles in histories.items():
        labels = labels_map.get(sym) or []
        off = (offset_map or {}).get(sym, 0)
        segs = []
        for (s, e, rid) in regime.segments_from_labels(labels):
            gs, ge = s + off, e + off
            w0 = max(gs - WARMUP_BARS, 0)
            segs.append({"regime": rid, "start_ts": candles[gs]["timestamp"],
                         "candles": candles[w0:ge], "n_bars": ge - gs})
        out[sym] = segs
    return out


def _provider_for(strategy, candles, settings, sym):
    try:
        fs = fast_sim.FastSeries(candles)
        if getattr(strategy, "IS_CUSTOM", False):
            return fast_sim.build_signal_provider(strategy.definition, fs), fs
        return fast_sim.build_builtin_signal_provider(strategy, fs, settings, sym), fs
    except Exception:  # noqa: BLE001 – Fallback: normale Simulation
        return None, None


def prepare_providers(strategy, segments: Dict[str, List[Dict]], settings: Dict):
    """FastSeries + Signal-Provider je Segment EINMAL bauen. Signale hängen nur
    von den Regeln ab (nicht von den Trade-Parametern) und werden für alle
    Kandidaten wiederverwendet; Regel-Varianten teilen sich die FastSeries."""
    for sym, segs in segments.items():
        for seg in segs:
            try:
                seg["fs"] = fast_sim.FastSeries(seg["candles"])
            except Exception:  # noqa: BLE001
                seg["fs"] = None
            seg["provider"] = provider_for_seg(strategy, seg, settings, sym)


def _def_key(strategy) -> str:
    if getattr(strategy, "IS_CUSTOM", False):
        d = strategy.definition
        return json.dumps({"i": d.get("indicators"), "l": d.get("long_rules"),
                           "s": d.get("short_rules")}, sort_keys=True, default=str)
    return getattr(strategy, "STRATEGY_ID", "builtin")


def provider_for_seg(strategy, seg: Dict, settings: Dict, sym: str):
    """Signal-Provider für (Strategie-Variante, Segment) – gecacht.
    Die FastSeries des Segments wird wiederverwendet, dadurch kostet eine neue
    Regel-Variante nur noch die Regel-Auswertung, nicht die Indikator-Berechnung."""
    fs = seg.get("fs")
    if fs is None:
        try:
            fs = fast_sim.FastSeries(seg["candles"])
        except Exception:  # noqa: BLE001
            fs = None
        seg["fs"] = fs
    if fs is None:
        return None
    cache = seg.setdefault("_prov", {})
    key = _def_key(strategy)
    if key in cache:
        return cache[key]
    try:
        if getattr(strategy, "IS_CUSTOM", False):
            prov = fast_sim.build_signal_provider(strategy.definition, fs)
        else:
            prov = fast_sim.build_builtin_signal_provider(strategy, fs, settings, sym)
    except Exception:  # noqa: BLE001
        prov = None
    if len(cache) >= MAX_PROVIDER_CACHE:
        for k in list(cache)[:-(MAX_PROVIDER_CACHE - 1)]:
            cache.pop(k, None)
    cache[key] = prov
    return prov


def simulate_segment(strategy, seg: Dict, sym: str, settings: Dict, cfg: Dict,
                     should_stop=None, provider=None) -> List[Dict]:
    """Ein Segment simulieren; nur Trades zählen, die IM Segment geöffnet wurden
    (Warmup-Trades werden verworfen)."""
    if provider is None:
        provider = provider_for_seg(strategy, seg, settings, sym)
    res = simulate_pair(strategy, seg["candles"], sym, settings, cfg,
                        None, True, should_stop, provider)
    start_iso = _iso(seg["start_ts"])
    return [t for t in (res.get("all_trades") or [])
            if (t.get("opened") or "") >= start_iso]


async def eval_regime_config(strategy, segments: Dict[str, List[Dict]], rid: int,
                             settings: Dict, cfg: Dict, should_stop=None) -> Dict:
    """Eine Trade-Konfiguration auf allen Segmenten EINES Regimes bewerten."""
    rows = []
    for sym, segs in segments.items():
        for seg in segs:
            if seg["regime"] != rid:
                continue
            if should_stop and should_stop():
                raise JobCancelled()
            rows.extend(await asyncio.to_thread(
                simulate_segment, strategy, seg, sym, settings, cfg, should_stop))
    return metrics_from_rows(rows, cfg.get("max_capital", 100))


async def eval_dynamic(strategy, segments: Dict[str, List[Dict]],
                       configs: Dict[int, Dict], base_cfg: Dict, settings: Dict,
                       should_stop=None, strategies_by_regime: Dict = None
                       ) -> Tuple[Dict, List[Dict]]:
    """Komplette dynamische Simulation: jedes Segment mit der Sub-Strategie und
    Konfiguration seines Regimes; chronologisch zusammengeführt."""
    rows = []
    for sym, segs in segments.items():
        for seg in segs:
            if should_stop and should_stop():
                raise JobCancelled()
            tp = configs.get(seg["regime"]) or {}
            cfg = {**base_cfg, **tp}
            st = (strategies_by_regime or {}).get(seg["regime"]) or strategy
            for t in await asyncio.to_thread(simulate_segment, st, seg, sym,
                                             settings, cfg, should_stop, None):
                rows.append({**t, "symbol": sym, "regime": seg["regime"]})
    return metrics_from_rows(rows, base_cfg.get("max_capital", 100)), rows


def _score(m: Dict, objective: str) -> float:
    wr = m.get("win_rate", 0.0)
    pnl = m.get("pnl", 0.0)
    if objective == "win_rate":
        return wr * 1000 + pnl
    if objective == "pnl":
        return pnl
    return pnl * (0.5 + wr / 100.0)


def sample_config(trade_space: Dict, rng: random.Random) -> Dict:
    tp = {k: rng.choice(v) for k, v in trade_space.items()}
    if isinstance(tp.get("tp_full_crv"), (int, float)) and isinstance(tp.get("tp1_crv"), (int, float)) \
            and tp["tp_full_crv"] < tp["tp1_crv"]:
        tp["tp_full_crv"], tp["tp1_crv"] = tp["tp1_crv"], tp["tp_full_crv"]
    return tp


def split_segments(segments: Dict[str, List[Dict]], train_pct: float
                   ) -> Tuple[Dict[str, List[Dict]], Dict[str, List[Dict]]]:
    """Jeden Regime-Abschnitt chronologisch in Trainings- und Validierungsteil
    schneiden. Beide Teile behalten ihren Warmup-Vorlauf, damit Indikatoren
    korrekt anlaufen. So kann eine Sub-Strategie innerhalb IHRER Marktphase
    auf unbekannten Daten geprüft werden (Walk-Forward je Regime)."""
    pct = min(max(float(train_pct), 40.0), 95.0) / 100.0
    train: Dict[str, List[Dict]] = {}
    val: Dict[str, List[Dict]] = {}
    for sym, segs in segments.items():
        tr, va = [], []
        for seg in segs:
            c = seg["candles"]
            n_bars = int(seg["n_bars"])
            warm = max(len(c) - n_bars, 0)
            cut = int(n_bars * pct)
            if cut > 20:
                tr.append({"regime": seg["regime"], "start_ts": c[warm]["timestamp"],
                           "candles": c[:warm + cut], "n_bars": cut})
            if n_bars - cut > 20:
                s = warm + cut
                w0 = max(s - WARMUP_BARS, 0)
                va.append({"regime": seg["regime"], "start_ts": c[s]["timestamp"],
                           "candles": c[w0:], "n_bars": n_bars - cut})
        if tr:
            train[sym] = tr
        if va:
            val[sym] = va
    return train, val


def validation_passed(m: Dict, min_trades: int) -> bool:
    """Bestanden = auf den unbekannten Daten der eigenen Marktphase profitabel
    und mit genügend Trades (kein Zufallstreffer)."""
    return (m.get("trades") or 0) >= max(min_trades, 3) and (m.get("pnl") or 0) > 0


def _guarded_score(m: Dict, objective: str, min_trades: int) -> float:
    """Score mit Mindest-Trade-Filter (wie im Optimizer)."""
    if (m.get("trades") or 0) < min_trades:
        return -1e9 + (m.get("trades") or 0)
    return _score(m, objective)


async def discover_regime_strategy(segments: Dict[str, List[Dict]], rid: int,
                                   settings: Dict, base_cfg: Dict,
                                   indicators: List[str], base_definition: Optional[Dict],
                                   objective: str, min_trades: int,
                                   max_rules: int = 4, weights: Dict = None,
                                   progress=None, should_stop=None,
                                   val_segments: Dict[str, List[Dict]] = None) -> Dict:
    """Vollständige eigene Strategie für EIN Regime entdecken.

    Gleicher Greedy-Algorithmus wie die globale Discovery, aber ausschließlich
    auf den Kerzen-Abschnitten dieses Regimes bewertet. Ergebnis ist eine
    eigenständige Custom-Strategie (eigene Regeln), die anschließend noch eigene
    Trade-Parameter bekommt.

    Ist `val_segments` gesetzt, wird nach jeder Regel-Runde auf den unbekannten
    Daten DERSELBEN Marktphase geprüft (Walk-Forward je Regime). Zurückgegeben
    wird die letzte Variante, die diese Prüfung bestanden hat – lieber weniger
    Regeln als eine überangepasste Strategie.
    """
    from services.optimizer import _mk_strategy, build_candidates

    cands = build_candidates(indicators or None)
    w = weights or {}
    cands.sort(key=lambda c: -w.get(c["ind"], 1.0))
    if base_definition:
        definition = copy.deepcopy(base_definition)
        definition.setdefault("indicators", {})
        definition.setdefault("long_rules", [])
        definition.setdefault("short_rules", [])
    else:
        definition = {"name": f"Regime {rid + 1}", "indicators": {},
                      "long_rules": [], "short_rules": []}
    definition["id"] = f"dyn_regime_{rid}"

    min_val_trades = max(int(min_trades * 0.4), 3)

    async def validate(d):
        if not val_segments:
            return None, True
        m = await eval_regime_config(_mk_strategy(d), val_segments, rid,
                                     settings, base_cfg, should_stop)
        return m, validation_passed(m, min_val_trades)

    best_score, best_metrics = -1e18, {"trades": 0}
    steps: List[Dict] = []
    passed: List[Dict] = []   # Snapshots, die die Validierung bestanden haben
    if definition["long_rules"] or definition["short_rules"]:
        m0 = await eval_regime_config(_mk_strategy(definition), segments, rid,
                                      settings, base_cfg, should_stop)
        best_score, best_metrics = _guarded_score(m0, objective, min_trades), m0
        vm, ok = await validate(definition)
        steps.append({"round": 0, "added": "Basis-Strategie", "metrics": m0,
                      "score": round(best_score, 3), "validation": vm,
                      "validation_passed": ok})
        if ok:
            passed.append({"definition": copy.deepcopy(definition), "metrics": m0,
                           "score": best_score, "validation": vm, "rules": []})
    used = set()
    for round_i in range(max_rules):
        round_best = None
        for cand in cands:
            if cand["label"] in used:
                continue
            if should_stop and should_stop():
                raise JobCancelled()
            d = {**definition,
                 "long_rules": definition["long_rules"] + [dict(cand["long"])],
                 "short_rules": definition["short_rules"] + [dict(cand["short"])]}
            try:
                m = await eval_regime_config(_mk_strategy(d), segments, rid,
                                             settings, base_cfg, should_stop)
            except JobCancelled:
                raise
            except Exception:  # noqa: BLE001 – einzelne Kandidaten isolieren
                continue
            if progress:
                progress(1)
            sc = _guarded_score(m, objective, min_trades)
            if round_best is None or sc > round_best[0]:
                round_best = (sc, cand, m)
        if round_best is None or round_best[0] <= best_score + 1e-9:
            steps.append({"round": round_i + 1, "added": None,
                          "info": "Keine weitere Regel verbessert diese Marktphase"})
            break
        best_score, cand, best_metrics = round_best[0], round_best[1], round_best[2]
        definition["long_rules"].append(dict(cand["long"]))
        definition["short_rules"].append(dict(cand["short"]))
        used.add(cand["label"])
        vm, ok = await validate(definition)
        steps.append({"round": round_i + 1, "added": cand["label"],
                      "score": round(best_score, 3), "metrics": best_metrics,
                      "validation": vm, "validation_passed": ok})
        if ok:
            passed.append({"definition": copy.deepcopy(definition),
                           "metrics": best_metrics, "score": best_score,
                           "validation": vm,
                           "rules": [s["added"] for s in steps if s.get("added")
                                     and s["added"] != "Basis-Strategie"]})

    if val_segments:
        if not passed:
            return {"definition": None, "metrics": best_metrics, "score": round(best_score, 3),
                    "steps": steps, "rules": [], "validation": None,
                    "validation_passed": False,
                    "note": "Keine Regel-Kombination hat den Walk-Forward dieser "
                            "Marktphase bestanden – Basis-Strategie bleibt aktiv"}
        best = passed[-1]
        return {"definition": best["definition"], "metrics": best["metrics"],
                "score": round(best["score"], 3), "steps": steps,
                "rules": best["rules"], "validation": best["validation"],
                "validation_passed": True}
    return {"definition": definition, "metrics": best_metrics,
            "score": round(best_score, 3), "steps": steps,
            "validation": None, "validation_passed": None,
            "rules": [s["added"] for s in steps if s.get("added")
                      and s["added"] != "Basis-Strategie"]}



async def optimize_regime(job, strategy, segments, rid: int, settings, base_cfg,
                          trade_space, iterations: int, objective: str,
                          min_trades: int, progress=None, should_stop=None,
                          val_segments=None) -> Dict:
    """Random-Search der Trade-Parameter NUR auf den Daten eines Regimes.
    Zu wenig Trades -> Regime als 'insufficient' markiert (Fallback greift).
    Mit `val_segments` muss die gewählte Konfiguration zusätzlich auf den
    unbekannten Daten derselben Marktphase profitabel sein (Walk-Forward)."""
    rng = random.Random(1000 + rid)
    base_m = await eval_regime_config(strategy, segments, rid, settings, base_cfg,
                                      should_stop)
    cands = [({}, base_m, _score(base_m, objective))]
    for it in range(iterations):
        if should_stop and should_stop():
            raise JobCancelled()
        tp = sample_config(trade_space, rng)
        m = await eval_regime_config(strategy, segments, rid, settings,
                                     {**base_cfg, **tp}, should_stop)
        if m["trades"] >= min_trades:
            cands.append((tp, m, _score(m, objective)))
        if progress:
            progress(it + 1)
    cands.sort(key=lambda x: (-(x[1]["trades"] >= min_trades), -x[2]))
    best_tp, best_m, best_sc = cands[0]
    validation = None
    validated = None
    if val_segments:
        min_val = max(int(min_trades * 0.4), 3)
        validated = False
        for tp, m, sc in cands[:8]:
            vm = await eval_regime_config(strategy, val_segments, rid, settings,
                                          {**base_cfg, **tp}, should_stop)
            if validation_passed(vm, min_val):
                best_tp, best_m, best_sc, validation, validated = tp, m, sc, vm, True
                break
            if validation is None:
                validation = vm
    insufficient = best_m["trades"] < min_trades
    return {"regime": rid, "config": best_tp, "metrics": best_m,
            "baseline_metrics": base_m, "insufficient": insufficient,
            "validation": validation, "validation_passed": validated,
            "score": round(best_sc, 3)}


async def optimize_static(strategy, full_train: Dict[str, List[Dict]], settings,
                          base_cfg, trade_space, iterations: int, objective: str,
                          min_trades: int, progress=None, should_stop=None) -> Dict:
    """Statische Benchmark: beste EINZELNE Konfiguration auf den gesamten
    Trainingsdaten (gleiches Suchbudget wie ein Regime)."""
    providers = {sym: _provider_for(strategy, c, settings, sym)[0]
                 for sym, c in full_train.items()}

    async def _eval(cfg):
        rows = []
        for sym, candles in full_train.items():
            if should_stop and should_stop():
                raise JobCancelled()
            res = await asyncio.to_thread(simulate_pair, strategy, candles, sym,
                                          settings, cfg, None, True, should_stop,
                                          providers.get(sym))
            rows.extend(res.get("all_trades") or [])
        return metrics_from_rows(rows, cfg.get("max_capital", 100))

    rng = random.Random(7)
    best_tp, best_m = {}, await _eval(base_cfg)
    best_sc = _score(best_m, objective)
    for it in range(iterations):
        tp = sample_config(trade_space, rng)
        m = await _eval({**base_cfg, **tp})
        if m["trades"] >= min_trades:
            sc = _score(m, objective)
            if sc > best_sc or best_m["trades"] < min_trades:
                best_sc, best_tp, best_m = sc, tp, m
        if progress:
            progress(it + 1)
    return {"config": best_tp, "metrics": best_m, "score": round(best_sc, 3)}


async def optimize_regime_rules(strategy, segments, rid: int, settings, base_cfg,
                                config: Dict, indicators: List[str],
                                min_trades: int, base_metrics: Dict,
                                objective: str, weights: Dict[str, float] = None,
                                max_candidates: int = 25, progress=None,
                                should_stop=None) -> Optional[Dict]:
    """Regel-Variante je Regime (nur Custom-Strategien): testet, ob EINE
    zusätzliche Regel aus den gewählten Indikatoren die Performance in DIESEM
    Regime deutlich verbessert (>10%). Kandidaten werden nach dem
    Lern-Gedächtnis sortiert (historisch erfolgreiche Indikatoren zuerst)."""
    if not getattr(strategy, "IS_CUSTOM", False):
        return None
    from services.optimizer import build_candidates, _mk_strategy
    cands = build_candidates(indicators or None)
    w = weights or {}
    cands.sort(key=lambda c: -w.get(c["ind"], 1.0))
    cands = cands[:max_candidates]
    base_def = strategy.definition
    cfg = {**base_cfg, **(config or {})}
    base_sc = _score(base_metrics, objective)
    best = None
    for c in cands:
        if should_stop and should_stop():
            raise JobCancelled()
        var_def = copy.deepcopy(base_def)
        var_def["id"] = "opt_eval"
        var_def.setdefault("long_rules", []).append(dict(c["long"]))
        var_def.setdefault("short_rules", []).append(dict(c["short"]))
        st_v = _mk_strategy(var_def)
        rows = []
        try:
            for sym, segs in segments.items():
                for seg in segs:
                    if seg["regime"] != rid:
                        continue
                    prov = None
                    if seg.get("fs") is not None:
                        try:
                            prov = fast_sim.build_signal_provider(var_def, seg["fs"])
                        except Exception:  # noqa: BLE001
                            prov = None
                    rows.extend(await asyncio.to_thread(
                        simulate_segment, st_v, seg, sym, settings, cfg,
                        should_stop, prov))
        except JobCancelled:
            raise
        except Exception:  # noqa: BLE001 – einzelne Kandidaten isolieren
            continue
        m = metrics_from_rows(rows, cfg.get("max_capital", 100))
        if progress:
            progress(1)
        if m["trades"] < min_trades:
            continue
        sc = _score(m, objective)
        if sc > base_sc * 1.1 + 1e-9 and (best is None or sc > best["score"]):
            best = {"rule_label": c["label"], "rule_long": c["long"],
                    "rule_short": c["short"], "definition": var_def,
                    "metrics": m, "score": round(sc, 3),
                    "improvement_pct": round((sc - base_sc) / max(abs(base_sc), 1e-9) * 100, 1)}
    return best


def build_verdict(dyn_test: Dict, stat_test: Dict, n_regimes: int,
                  switches: int) -> Dict:
    """Ehrlicher Vergleich: dynamisch nur empfehlen, wenn auf den UNBEKANNTEN
    Testdaten klar besser (PnL höher, Drawdown nicht deutlich schlechter)."""
    dp, sp = float(dyn_test.get("pnl") or 0), float(stat_test.get("pnl") or 0)
    dd_d = float(dyn_test.get("max_drawdown") or 0)
    dd_s = float(stat_test.get("max_drawdown") or 0)
    reasons = []
    positive = dp > 0
    better_pnl = dp > sp * 1.05 if sp > 0 else dp > sp
    dd_ok = dd_d <= max(dd_s * 1.25, dd_s + 0.5)
    enough = (dyn_test.get("trades") or 0) >= 5
    if not positive:
        reasons.append(f"Dynamisches Test-PnL ist negativ ({dp:.2f}) – kein Mehrwert nachweisbar")
    if not enough:
        reasons.append(f"Zu wenige Test-Trades ({dyn_test.get('trades')}) für eine belastbare Aussage")
    if better_pnl:
        reasons.append(f"Test-PnL dynamisch {dp:.2f} vs. statisch {sp:.2f}")
    else:
        reasons.append(f"Dynamisch NICHT klar besser im Test-PnL ({dp:.2f} vs. {sp:.2f})")
    if not dd_ok:
        reasons.append(f"Drawdown dynamisch schlechter ({dd_d:.2f} vs. {dd_s:.2f})")
    dynamic_better = bool(better_pnl and dd_ok and enough and positive)
    rec = ("Dynamische Strategie empfohlen – sie schlägt die statische Benchmark "
           "auf unbekannten Testdaten." if dynamic_better else
           "Statische Strategie bevorzugen – die dynamische Variante bringt auf den "
           "Testdaten keinen nachweisbaren Mehrwert. Komplexität nur erhöhen, wenn "
           "sie nachweislich bessere Ergebnisse liefert.")
    return {"dynamic_better": dynamic_better, "reasons": reasons,
            "recommendation": rec, "regimes": n_regimes, "test_switches": switches}
