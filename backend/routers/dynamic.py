"""Dynamische Strategien: Verwaltung, Live-Regime-Erkennung & Konfig-Umschaltung.

- POST /api/dynamic/save            gespeicherte dynamische Strategie anlegen
- GET  /api/dynamic/list            alle dynamischen Strategien
- POST /api/dynamic/{id}/refresh    aktuelles Regime je Coin neu bestimmen
                                    (Wechsel werden protokolliert)
- POST /api/dynamic/{id}/apply      aktive Regime-Konfiguration als Coin-Override
                                    für Live/Paper übernehmen
- POST /api/dynamic/{id}/settings   Auto-Prüfung/Auto-Übernahme konfigurieren
- GET  /api/dynamic/{id}/log        Wechsel-Protokoll
- GET  /api/learning/summary        Lern-Gedächtnis (Robustheit je Marktphase)
- DELETE /api/dynamic/{id}
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.utils import _clean
from services import dynamic_live, learning
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
           "rule_variants": body.get("rule_variants") or {},
           "settings": {**(body.get("settings") or {}),
                        "auto_check_enabled": False, "auto_apply_enabled": False,
                        "check_interval_minutes": 60, "check_days": 30},
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
    await state.db.dynamic_switch_log.delete_many({"dynamic_id": did})
    return {"status": "deleted"}


async def _get_doc(did: str) -> Dict:
    doc = await state.db.dynamic_strategies.find_one({"id": did})
    if not doc:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return doc


@router.post("/api/dynamic/{did}/refresh")
async def dynamic_refresh(did: str, body: Dict = None):
    doc = await _get_doc(did)
    days = int(min(max(int((body or {}).get("days") or 30), 7), 90))
    try:
        res = await dynamic_live.check_one(doc, days, auto_apply=False)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": did, "switches": res["switches"], **res["state"]}


@router.post("/api/dynamic/{did}/apply")
async def dynamic_apply(did: str, _: bool = Depends(require_admin)):
    """Aktive Regime-Konfiguration je Coin als Live/Paper-Override übernehmen."""
    doc = await _get_doc(did)
    try:
        applied = await dynamic_live.apply_configs(doc)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "strategy_id": doc["strategy_id"], "applied": applied}


@router.post("/api/dynamic/{did}/settings")
async def dynamic_settings(did: str, body: Dict, _: bool = Depends(require_admin)):
    """Auto-Prüfung im Hintergrund konfigurieren (Intervall, Auto-Übernahme)."""
    doc = await _get_doc(did)
    s = dict(doc.get("settings") or {})
    if "auto_check_enabled" in body:
        s["auto_check_enabled"] = bool(body["auto_check_enabled"])
    if "auto_apply_enabled" in body:
        s["auto_apply_enabled"] = bool(body["auto_apply_enabled"])
    if body.get("check_interval_minutes") is not None:
        s["check_interval_minutes"] = int(min(max(int(body["check_interval_minutes"]), 5), 1440))
    if body.get("check_days") is not None:
        s["check_days"] = int(min(max(int(body["check_days"]), 7), 90))
    await state.db.dynamic_strategies.update_one({"id": did}, {"$set": {"settings": s}})
    return {"status": "success", "settings": s}


@router.get("/api/dynamic/{did}/log")
async def dynamic_log(did: str, limit: int = 100):
    """Wechsel-Protokoll: alle Regime-Wechsel mit Datum, Sicherheit & Begründung."""
    rows = await state.db.dynamic_switch_log.find({"dynamic_id": did}) \
        .sort("at", -1).to_list(int(min(max(limit, 1), 500)))
    return {"log": [_clean(r) for r in rows]}


@router.get("/api/learning/summary")
async def learning_summary():
    """Lern-Gedächtnis: welche Indikatoren/Strategien liefen je Marktphase am besten."""
    return await learning.summary(state.db)
