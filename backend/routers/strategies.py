"""Strategie-Endpoints: Liste, Custom-CRUD, Export/Import, Coin-Toggles."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.config import ALL_SYMBOLS
from core.state import scanner, autotrader, strategy_coin_toggles
from strategies.registry import registry as strategy_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["strategies"])


@router.get("/api/strategies")
async def get_strategies():
    out = []
    deleted = set(scanner.settings.get("deleted_strategies", []))
    for meta in strategy_registry.list_all():
        if meta["id"] in deleted:
            continue
        strat = strategy_registry.get(meta["id"])
        item = {**meta, "current_params": strat.get_params(scanner.settings)}
        if getattr(strat, "IS_CUSTOM", False):
            item["definition"] = strat.definition
        out.append(item)
    return {"strategies": out,
            "active": scanner.settings.get("active_strategy", "scalping_4_rules"),
            "enabled": scanner.enabled_strategies(),
            "signals_enabled": scanner.settings.get("strategy_signals_enabled", {})}


# ---- custom strategy CRUD ----
@router.post("/api/strategies/custom")
async def create_custom_strategy(definition: Dict, _: bool = Depends(require_admin)):
    sid = definition.get("id") or f"custom_{uuid.uuid4().hex[:8]}"
    definition["id"] = sid
    definition.setdefault("timeframe", "1m")
    await state.db.custom_strategies.update_one({"id": sid}, {"$set": definition}, upsert=True)
    strategy_registry.upsert_custom(definition)
    # Timeframe-Konsistenz: definition.timeframe ist die eine Quelle der Wahrheit,
    # strategy_timeframes wird synchron gehalten (Export/Backtester/Scanner).
    tfs = dict(scanner.settings.get("strategy_timeframes", {}))
    if tfs.get(sid) != definition["timeframe"]:
        tfs[sid] = definition["timeframe"]
        scanner.update_settings({"strategy_timeframes": tfs})
    # auto-enable in tabs
    enabled = scanner.settings.get("enabled_strategies", [])
    if sid not in enabled:
        enabled.append(sid)
        scanner.update_settings({"enabled_strategies": enabled})
    await state.db.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success", "id": sid, "definition": definition}


@router.post("/api/strategies/{strategy_id}/duplicate")
async def duplicate_strategy(strategy_id: str, body: Dict = None, _: bool = Depends(require_admin)):
    """Strategie duplizieren: legt eine unabhängige Kopie an (inkl. Parameter,
    Timeframe, Zeitfenster und Backtest-Einstellungen). Nur Custom/Discovery-
    Strategien haben eine kopierbare Regel-Definition."""
    body = body or {}
    strat = strategy_registry.get(strategy_id)
    if not strat:
        raise HTTPException(status_code=404, detail="Strategie nicht gefunden")
    if not getattr(strat, "IS_CUSTOM", False):
        raise HTTPException(status_code=400,
                            detail="Nur Custom/Discovery-Strategien können dupliziert werden")
    new_id = f"custom_{uuid.uuid4().hex[:8]}"
    definition = dict(strat.definition)
    definition["id"] = new_id
    base_name = definition.get("name") or strategy_id
    definition["name"] = body.get("name") or f"{base_name} (Kopie)"
    definition.setdefault("timeframe", "1m")
    await state.db.custom_strategies.update_one({"id": new_id}, {"$set": definition}, upsert=True)
    strategy_registry.upsert_custom(definition)

    s = scanner.settings
    updates: Dict = {}
    sp = s.get("strategy_params", {}).get(strategy_id)
    if sp:
        all_sp = dict(s.get("strategy_params", {}))
        all_sp[new_id] = dict(sp)
        updates["strategy_params"] = all_sp
    cp = s.get("coin_params", {}).get(strategy_id)
    if cp:
        all_cp = dict(s.get("coin_params", {}))
        all_cp[new_id] = dict(cp)
        updates["coin_params"] = all_cp
    tfs = dict(s.get("strategy_timeframes", {}))
    tfs[new_id] = tfs.get(strategy_id) or definition.get("timeframe") or "1m"
    updates["strategy_timeframes"] = tfs
    ss = s.get("strategy_sessions", {}).get(strategy_id)
    if ss:
        all_ss = dict(s.get("strategy_sessions", {}))
        all_ss[new_id] = list(ss)
        updates["strategy_sessions"] = all_ss
    enabled = list(s.get("enabled_strategies", []))
    if new_id not in enabled:
        enabled.append(new_id)
    updates["enabled_strategies"] = enabled
    scanner.update_settings(updates)
    await state.db.settings.update_one({"_id": "scanner_settings"},
                                       {"$set": scanner.settings}, upsert=True)

    # Backtest-Einstellungen mitkopieren
    bt_doc = await state.db.settings.find_one({"_id": "backtest_strategy_configs"})
    configs = (bt_doc or {}).get("configs", {})
    if configs.get(strategy_id):
        configs[new_id] = dict(configs[strategy_id])
        await state.db.settings.update_one({"_id": "backtest_strategy_configs"},
                                           {"$set": {"configs": configs}}, upsert=True)

    # Live/Paper-Strategie-Override mitkopieren (Modus bleibt sicherheitshalber 'off')
    override = autotrader.config.get("strategy_overrides", {}).get(strategy_id)
    if override:
        overrides = dict(autotrader.config.get("strategy_overrides", {}))
        copied = dict(override)
        copied["mode"] = "off"
        copied["enabled"] = False
        overrides[new_id] = copied
        new_cfg = {"mode": autotrader.config.get("mode", "paper"),
                   "coins": autotrader.config.get("coins", {}),
                   "strategy_overrides": overrides}
        autotrader.set_config(new_cfg)
        await state.db.settings.update_one(
            {"_id": "autotrade_config"},
            {"$set": {"mode": new_cfg["mode"], "coins": new_cfg["coins"],
                      "strategy_overrides": overrides}}, upsert=True)

    return {"status": "success", "id": new_id, "name": definition["name"],
            "definition": definition}


@router.delete("/api/strategies/custom/{strategy_id}")
async def delete_custom_strategy(strategy_id: str, _: bool = Depends(require_admin)):
    await state.db.custom_strategies.delete_one({"id": strategy_id})
    strategy_registry.remove_custom(strategy_id)
    enabled = [s for s in scanner.settings.get("enabled_strategies", []) if s != strategy_id]
    scanner.update_settings({"enabled_strategies": enabled})
    await state.db.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success"}


@router.delete("/api/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str, _: bool = Depends(require_admin)):
    """Delete ANY strategy permanently. Custom => removed from DB.
    Built-in (predefined) => added to deleted_strategies so it never shows/runs."""
    is_custom = strategy_id in strategy_registry._custom_ids
    if is_custom:
        await state.db.custom_strategies.delete_one({"id": strategy_id})
        strategy_registry.remove_custom(strategy_id)
    else:
        deleted = list(scanner.settings.get("deleted_strategies", []))
        if strategy_id not in deleted:
            deleted.append(strategy_id)
        scanner.update_settings({"deleted_strategies": deleted})
    enabled = [s for s in scanner.settings.get("enabled_strategies", []) if s != strategy_id]
    scanner.update_settings({"enabled_strategies": enabled})
    await state.db.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success", "id": strategy_id, "was_custom": is_custom}


@router.post("/api/strategies/restore-defaults")
async def restore_default_strategies(_: bool = Depends(require_admin)):
    """Un-delete all previously deleted built-in strategies."""
    scanner.update_settings({"deleted_strategies": []})
    await state.db.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success", "restored": True}


@router.get("/api/strategies/builder-options")
async def builder_options():
    from strategies.custom_strategy import INDICATORS, OPERATORS, INDICATOR_META, PERIOD_FIELDS
    return {"indicators": INDICATORS, "operators": OPERATORS,
            "indicator_meta": INDICATOR_META, "period_fields": PERIOD_FIELDS}


# ---- Strategie-Backup: kompletter Export/Import pro Strategie ----
@router.get("/api/strategies/{strategy_id}/export")
async def export_strategy(strategy_id: str):
    """Komplette Strategie als Backup exportieren: Definition/Regeln, Parameter,
    Timeframe, Zeitfenster, Live/Paper-Trade-Einstellungen (global + pro Coin)
    und Backtest-Einstellungen. Ziel: 1:1-Wiederherstellung nach Löschung."""
    strat = strategy_registry.get(strategy_id)
    if not strat:
        raise HTTPException(status_code=404, detail="Strategie nicht gefunden")
    s = scanner.settings
    coin_cfgs = {}
    docs = await state.db.strategy_coin_configs.find().to_list(2000)
    prefix = f"{strategy_id}_"
    for d in docs:
        key = d.get("_id") or ""
        if key.startswith(prefix):
            sym = key[len(prefix):]
            if sym in ALL_SYMBOLS:
                coin_cfgs[sym] = d.get("config", {})
    bt_doc = await state.db.settings.find_one({"_id": "backtest_strategy_configs"})
    definition = getattr(strat, "definition", None)
    # BUGFIX Export-Timeframe: eine autoritative Quelle statt drei potenziell
    # widersprüchlicher Werte. Reihenfolge: explizit gesetzter Timeframe
    # (strategy_timeframes) > definition.timeframe > Strategie-Default.
    effective_tf = (s.get("strategy_timeframes", {}).get(strategy_id)
                    or (definition or {}).get("timeframe")
                    or getattr(strat, "STRATEGY_TIMEFRAME", "1m"))
    if isinstance(definition, dict):
        definition = {**definition, "timeframe": effective_tf}
    return {
        "type": "strategy_backup",
        "version": 2,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": strategy_id,
        "name": getattr(strat, "STRATEGY_NAME", strategy_id),
        "is_custom": strategy_id in strategy_registry._custom_ids,
        "definition": definition,
        "strategy_params": s.get("strategy_params", {}).get(strategy_id, {}),
        "coin_params": s.get("coin_params", {}).get(strategy_id, {}),
        "timeframe": effective_tf,
        "strategy_sessions": s.get("strategy_sessions", {}).get(strategy_id, []),
        "strategy_override": autotrader.config.get("strategy_overrides", {}).get(strategy_id, {}),
        "strategy_coin_configs": coin_cfgs,
        "backtest_config": ((bt_doc or {}).get("configs", {})).get(strategy_id, {}),
        "enabled_in_tabs": strategy_id in s.get("enabled_strategies", []),
    }


@router.post("/api/strategies/import")
async def import_strategy(body: Dict, _: bool = Depends(require_admin)):
    """Strategie-Backup importieren: stellt eine gelöschte Strategie inkl. aller
    Parameter/Einstellungen 1:1 wieder her bzw. überschreibt verstellte Werte."""
    if body.get("type") != "strategy_backup":
        raise HTTPException(status_code=400, detail="Keine gültige Strategie-Backup-Datei")
    sid = body.get("strategy_id")
    if not sid:
        raise HTTPException(status_code=400, detail="strategy_id fehlt in der Datei")
    definition = body.get("definition")
    is_custom = bool(body.get("is_custom")) or isinstance(definition, dict)
    # Timeframe-Konsistenz: body.timeframe hat Vorrang, sonst definition.timeframe
    effective_tf = body.get("timeframe") or (definition or {}).get("timeframe") if isinstance(definition, dict) \
        else body.get("timeframe")
    if is_custom and isinstance(definition, dict):
        definition = dict(definition)
        definition["id"] = sid
        if effective_tf:
            definition["timeframe"] = effective_tf
        definition.setdefault("timeframe", "1m")
        await state.db.custom_strategies.update_one({"id": sid}, {"$set": definition}, upsert=True)
        strategy_registry.upsert_custom(definition)
    elif not strategy_registry.get(sid):
        raise HTTPException(status_code=404,
                            detail="Built-in-Strategie existiert in dieser Version nicht")
    # gelöschte Built-ins reaktivieren
    updates: Dict = {"deleted_strategies":
                     [d for d in scanner.settings.get("deleted_strategies", []) if d != sid]}
    if body.get("strategy_params"):
        sp = dict(scanner.settings.get("strategy_params", {}))
        sp[sid] = body["strategy_params"]
        updates["strategy_params"] = sp
    if body.get("coin_params"):
        cp = dict(scanner.settings.get("coin_params", {}))
        cp[sid] = body["coin_params"]
        updates["coin_params"] = cp
    if effective_tf:
        tfs = dict(scanner.settings.get("strategy_timeframes", {}))
        tfs[sid] = effective_tf
        updates["strategy_timeframes"] = tfs
    if body.get("strategy_sessions"):
        ss = dict(scanner.settings.get("strategy_sessions", {}))
        ss[sid] = body["strategy_sessions"]
        updates["strategy_sessions"] = ss
    if body.get("enabled_in_tabs"):
        en = list(scanner.settings.get("enabled_strategies", []))
        if sid not in en:
            en.append(sid)
        updates["enabled_strategies"] = en
    scanner.update_settings(updates)
    await state.db.settings.update_one({"_id": "scanner_settings"},
                                       {"$set": scanner.settings}, upsert=True)
    if isinstance(body.get("strategy_override"), dict) and body["strategy_override"]:
        overrides = dict(autotrader.config.get("strategy_overrides", {}))
        overrides[sid] = body["strategy_override"]
        new_cfg = {"mode": autotrader.config.get("mode", "paper"),
                   "coins": autotrader.config.get("coins", {}),
                   "strategy_overrides": overrides}
        autotrader.set_config(new_cfg)
        await state.db.settings.update_one(
            {"_id": "autotrade_config"},
            {"$set": {"mode": new_cfg["mode"], "coins": new_cfg["coins"],
                      "strategy_overrides": overrides}}, upsert=True)
    n_coin = 0
    for sym, ccfg in (body.get("strategy_coin_configs") or {}).items():
        if sym not in ALL_SYMBOLS or not isinstance(ccfg, dict):
            continue
        key = f"{sid}_{sym}"
        await state.db.strategy_coin_configs.replace_one(
            {"_id": key}, {"_id": key, "config": ccfg}, upsert=True)
        autotrader.config.setdefault("strategy_coin_configs", {})[key] = ccfg
        n_coin += 1
    if isinstance(body.get("backtest_config"), dict) and body["backtest_config"]:
        doc = await state.db.settings.find_one({"_id": "backtest_strategy_configs"})
        configs = (doc or {}).get("configs", {})
        configs[sid] = body["backtest_config"]
        await state.db.settings.update_one({"_id": "backtest_strategy_configs"},
                                           {"$set": {"configs": configs}}, upsert=True)
    return {"status": "success", "id": sid, "name": body.get("name"),
            "restored_custom": is_custom, "coin_configs": n_coin}


# ---- NEW: per (strategy, coin) enable/disable toggle ----
@router.get("/api/strategies/{strategy_id}/coins")
async def get_strategy_coin_toggles(strategy_id: str):
    """Return {symbol: enabled} map for the given strategy across ALL_SYMBOLS.
    Missing rows default to True (kept enabled)."""
    result: Dict[str, bool] = {}
    for sym in ALL_SYMBOLS:
        result[sym] = strategy_coin_toggles.get((strategy_id, sym), True)
    return {"strategy_id": strategy_id, "coins": result}


@router.put("/api/strategies/{strategy_id}/coins/{symbol}")
async def set_strategy_coin_toggle(strategy_id: str, symbol: str,
                                   body: Dict, _: bool = Depends(require_admin)):
    """Enable/disable auto-trade + signals for ONE (strategy, coin) pair."""
    enabled = bool(body.get("enabled", True))
    now_iso = datetime.now(timezone.utc).isoformat()
    await state.db.strategy_coin_toggles.update_one(
        {"strategy_id": strategy_id, "symbol": symbol},
        {"$set": {"strategy_id": strategy_id, "symbol": symbol,
                  "enabled": enabled, "updated_at": now_iso}},
        upsert=True,
    )
    strategy_coin_toggles[(strategy_id, symbol)] = enabled
    return {"status": "success", "strategy_id": strategy_id,
            "symbol": symbol, "enabled": enabled}
