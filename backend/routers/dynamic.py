"""Dynamische Strategien: Verwaltung, Live-Regime-Erkennung & Konfig-Umschaltung.

- POST /api/dynamic/save            gespeicherte dynamische Strategie anlegen
- GET  /api/dynamic/list            alle dynamischen Strategien
- POST /api/dynamic/{id}/refresh    aktuelles Regime je Coin neu bestimmen
                                    (inkl. Sicherheit, Ähnlichkeiten, Vergleich
                                    aller Konfigurationen über die letzten X Tage)
- POST /api/dynamic/{id}/apply      aktive Regime-Konfiguration als Coin-Override
                                    für Live/Paper übernehmen
- DELETE /api/dynamic/{id}
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict

import aiohttp
from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.state import autotrader
from core.utils import _clean
from strategies.registry import registry as strategy_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dynamic"])


@router.post("/api/dynamic/save")
async def dynamic_save(body: Dict, _: bool = Depends(require_admin)):
    """Ergebnis eines Dynamik-Laufs als dynamische Strategie speichern."""
    for k in ("strategy_id", "model", "configs"):
        if not body.get(k):
            raise HTTPException(status_code=400, detail=f"{k} erforderlich")
    if not strategy_registry.get(body["strategy_id"]):
        raise HTTPException(status_code=400, detail="Strategie nicht gefunden")
    did = f"dyn_{uuid.uuid4().hex[:8]}"
    doc = {"id": did,
           "name": body.get("name") or f"Dynamisch: {body['strategy_id']}",
           "strategy_id": body["strategy_id"],
           "symbols": body.get("symbols") or [],
           "timeframe": body.get("timeframe") or "1m",
           "model": body["model"],
           "configs": body["configs"],
           "fallback_config": body.get("fallback_config") or {},
           "settings": body.get("settings") or {},
           "verdict": body.get("verdict") or {},
           "created_at": datetime.now(timezone.utc).isoformat(),
           "last_state": {}}
    await state.db.dynamic_strategies.replace_one({"id": did}, doc, upsert=True)
    return {"status": "success", "id": did}


@router.get("/api/dynamic/list")
async def dynamic_list():
    rows = await state.db.dynamic_strategies.find().sort("created_at", -1).to_list(100)
    out = []
    for r in rows:
        r = _clean(r)
        model = r.get("model") or {}
        out.append({**r, "model": {"regimes": model.get("regimes") or [],
                                   "silhouette": model.get("silhouette"),
                                   "lookback_days": model.get("lookback_days")}})
    return {"strategies": out}


@router.delete("/api/dynamic/{did}")
async def dynamic_delete(did: str, _: bool = Depends(require_admin)):
    res = await state.db.dynamic_strategies.delete_one({"id": did})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return {"status": "deleted"}


async def _refresh_state(doc: Dict, days: int) -> Dict:
    """Aktuelles Regime je Coin bestimmen + Info-Vergleich aller Konfigurationen
    über die letzten Tage (nur Anzeige – NICHT Grundlage der Umschaltung, um
    Overfitting auf die jüngste Vergangenheit zu vermeiden)."""
    import asyncio
    from services import regime as rg
    from services import dynamic_strategy as dyn
    from services.backtester import fetch_history, simulate_pair
    from services.timeframes import aggregate_candles
    from services.bitunix_trade import DEFAULT_COIN_CFG
    from core.state import scanner

    model = doc["model"]
    tf = doc.get("timeframe") or model.get("timeframe") or "1m"
    s = doc.get("settings") or {}
    conf_min = float(s.get("confidence_min") or 70) / 100.0
    min_hold = float(s.get("min_hold_days") or 2)
    strategy = strategy_registry.get(doc["strategy_id"])
    if not strategy:
        raise HTTPException(status_code=404, detail="Basis-Strategie nicht mehr vorhanden")
    configs = {int(k): v for k, v in (doc.get("configs") or {}).items()}
    cfg_base = {**DEFAULT_COIN_CFG}
    per_symbol = {}
    async with aiohttp.ClientSession() as session:
        for sym in doc.get("symbols") or []:
            try:
                raw = await fetch_history(session, sym, days)
                candles = aggregate_candles(raw, tf)
                del raw
                if len(candles) < 50:
                    per_symbol[sym] = {"error": "Zu wenig Daten"}
                    continue
                cur = rg.current_regime(model, candles, tf, conf_min, min_hold)
                rid = cur.get("regime")
                # {} = Baseline-Konfiguration ist gültig – nur bei UNBEKANNTEM Regime Fallback
                cur["active_config"] = configs[rid] if rid in configs \
                    else (doc.get("fallback_config") or {})
                # Info: wie hätten die anderen Konfigurationen zuletzt abgeschnitten?
                perf = []
                for r in model.get("regimes") or []:
                    c = configs.get(r["id"])
                    if c is None:
                        continue
                    res = await asyncio.to_thread(
                        simulate_pair, strategy, candles, sym,
                        dict(scanner.settings), {**cfg_base, **c}, None, False, None, None)
                    perf.append({"regime": r["id"], "label": r["label"],
                                 "pnl": res.get("pnl"), "trades": res.get("trades"),
                                 "win_rate": res.get("win_rate")})
                perf.sort(key=lambda x: -(x.get("pnl") or 0))
                cur["recent_performance"] = perf
                per_symbol[sym] = cur
            except Exception as e:  # noqa: BLE001 – pro Symbol isolieren
                logger.warning(f"dynamic refresh {sym} failed: {e}")
                per_symbol[sym] = {"error": str(e)[:200]}
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "days": days, "per_symbol": per_symbol}


@router.post("/api/dynamic/{did}/refresh")
async def dynamic_refresh(did: str, body: Dict = None):
    doc = await state.db.dynamic_strategies.find_one({"id": did})
    if not doc:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    days = int(min(max(int((body or {}).get("days") or 30), 7), 90))
    result = await _refresh_state(doc, days)
    await state.db.dynamic_strategies.update_one(
        {"id": did}, {"$set": {"last_state": result}})
    return {"id": did, **result}


@router.post("/api/dynamic/{did}/apply")
async def dynamic_apply(did: str, _: bool = Depends(require_admin)):
    """Aktive Regime-Konfiguration je Coin als Live/Paper-Override übernehmen
    (nutzt den zuletzt per Refresh bestimmten Zustand)."""
    from core.defaults import OPT_TRADE_KEYS
    doc = await state.db.dynamic_strategies.find_one({"id": did})
    if not doc:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    last = doc.get("last_state") or {}
    per_symbol = last.get("per_symbol") or {}
    if not per_symbol:
        raise HTTPException(status_code=400,
                            detail="Erst 'Regime aktualisieren' ausführen, dann übernehmen")
    sid = doc["strategy_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    applied = []
    for sym, st in per_symbol.items():
        cfg_r = (st or {}).get("active_config")
        if cfg_r is None:
            continue
        if not cfg_r:
            applied.append({"symbol": sym, "regime": st.get("regime"),
                            "label": st.get("label"), "confidence": st.get("confidence"),
                            "baseline": True})
            continue
        key = f"{sid}_{sym}"
        prev = await state.db.strategy_coin_configs.find_one({"_id": key})
        merged = dict((prev or {}).get("config", {}))
        for k in OPT_TRADE_KEYS:
            if cfg_r.get(k) is not None:
                merged[k] = cfg_r[k]
        merged["dynamic_applied"] = now_iso
        merged["dynamic_id"] = did
        merged["dynamic_regime"] = st.get("regime")
        await state.db.strategy_coin_configs.replace_one(
            {"_id": key}, {"_id": key, "config": merged}, upsert=True)
        autotrader.config.setdefault("strategy_coin_configs", {})[key] = merged
        applied.append({"symbol": sym, "regime": st.get("regime"),
                        "label": st.get("label"), "confidence": st.get("confidence")})
    await state.db.dynamic_strategies.update_one(
        {"id": did}, {"$set": {"last_applied": now_iso, "last_applied_info": applied}})
    return {"status": "success", "strategy_id": sid, "applied": applied}
