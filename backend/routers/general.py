"""Basis-Endpoints: Health, Coins, Klines, Signale, Settings, Session, System."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict

from core import state
from core.auth import require_admin
from core.config import TOP_10_COINS, OTHER_INSTRUMENTS, OTHER_YAHOO, ALL_SYMBOLS
from core.state import scanner, feed, telegram
from core.utils import _clean

logger = logging.getLogger(__name__)

router = APIRouter(tags=["general"])


@router.get("/")
async def root():
    return {"app": "Crypto Scalping Scanner", "status": "running"}


@router.get("/api/health")
async def health_check():
    return {"status": "alive"}


@router.get("/api/debug/status")
async def debug_status():
    from services import candle_cache as _cc
    return {"data_feed": feed.status, "session_active": scanner.is_trading_session(),
            "enabled_strategies": scanner.enabled_strategies(),
            "candle_cache": _cc.stats(),
            "coins": [scanner.debug_snapshot(s) for s in ALL_SYMBOLS]}


@router.get("/api/coins")
async def get_coins():
    return {"coins": TOP_10_COINS,
            "groups": [{"name": "TOP 10 COINS", "symbols": TOP_10_COINS},
                       {"name": "OTHER", "symbols": [{"symbol": i["symbol"], "name": i["name"]} for i in OTHER_INSTRUMENTS]}]}


@router.get("/api/klines/{symbol}")
async def get_klines(symbol: str, limit: int = 200):
    """Historical candles for the chart (fixes empty/black chart)."""
    try:
        if symbol in OTHER_YAHOO:
            candles = await feed.fetch_commodity(OTHER_YAHOO[symbol], "1d")
        else:
            candles = await feed.fetch(symbol, limit)
        return {"symbol": symbol, "candles": candles[-limit:]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/api/signals")
async def get_signals(limit: int = 50, strategy_id: str = None):
    q = {"trade_date": scanner.berlin_date()}
    if strategy_id:
        q["strategy_id"] = strategy_id
    signals = await state.db.signals.find(q).sort("timestamp", -1).limit(limit).to_list(limit)
    return {"signals": [_clean(s) for s in signals]}


@router.get("/api/rule-states")
async def get_rule_states(symbol: str = None):
    if symbol:
        return {"symbol": symbol, "states": scanner.rule_states.get(symbol, {})}
    return {"states": scanner.rule_states}


@router.get("/api/settings")
async def get_settings():
    return scanner.settings


@router.post("/api/settings")
async def update_settings(settings: Dict, _: bool = Depends(require_admin)):
    scanner.update_settings(settings)
    await state.db.settings.update_one({"_id": "scanner_settings"},
                                       {"$set": scanner.settings}, upsert=True)
    return {"status": "success", "settings": scanner.settings}


@router.get("/api/session/status")
async def session_status():
    now = scanner.berlin_now()
    return {"is_active": scanner.is_trading_session(),
            "current_session": scanner.get_current_session(),
            "custom_sessions": scanner.settings.get("custom_sessions", []),
            "pre_signal_enabled": scanner.settings.get("pre_signal_enabled", True),
            "berlin_time": now.strftime("%H:%M:%S"), "berlin_date": scanner.berlin_date()}


# ---------------- System / RAM / Cache ----------------
@router.get("/api/system/ram")
async def system_ram():
    """RAM-Auslastung inkl. Bewertung, was viel Speicher braucht."""
    import psutil
    from services import candle_cache
    from services import backtester as bt
    proc = psutil.Process()
    rss_mb = proc.memory_info().rss / 1024 / 1024
    vm = psutil.virtual_memory()
    cstats = candle_cache.stats()
    # ~500 Bytes pro Kerze im dict-Format (Messung)
    cache_mb = cstats["total_candles"] * 500 / 1024 / 1024
    export_candles = 0
    export_trades = 0
    for j in bt.JOBS.values():
        export_candles += sum(len(v) for v in (j.get("export_candles") or {}).values())
        export_trades += len(j.get("export_trades") or [])
    export_mb = export_candles * 500 / 1024 / 1024 + export_trades * 900 / 1024 / 1024
    return {
        "process_rss_mb": round(rss_mb, 1),
        "system_total_mb": round(vm.total / 1024 / 1024),
        "system_available_mb": round(vm.available / 1024 / 1024),
        "system_used_percent": vm.percent,
        "candle_cache": {
            "symbols": cstats["symbols"],
            "total_candles": cstats["total_candles"],
            "estimated_mb": round(cache_mb, 1),
            "max_candles": cstats["max_candles"],
            "disk_enabled": cstats["disk_enabled"],
        },
        "backtest_exports": {
            "candles": export_candles, "trades": export_trades,
            "estimated_mb": round(export_mb, 1),
        },
        "breakdown_hint": {
            "kerzen_cache": f"~{round(cache_mb, 1)} MB (größter Posten bei langen Zeiträumen)",
            "backtest_export": f"~{round(export_mb, 1)} MB (Kerzen/Trades des letzten Laufs für CSV)",
            "fast_path": "FastSeries: ~8 Bytes/Kerze pro Indikator-Serie, wird nach jedem "
                         "Symbol wieder freigegeben (gering)",
            "basis": "Python + Bibliotheken: ~150-250 MB Grundlast",
        },
    }


@router.post("/api/system/cache/clear")
async def system_cache_clear(_: bool = Depends(require_admin)):
    """Kerzen-Cache leeren + Backtest-Export-Rohdaten freigeben (RAM-Reset)."""
    import gc
    from services import candle_cache
    from services import backtester as bt
    before = candle_cache.stats()["total_candles"]
    candle_cache.clear()
    for j in bt.JOBS.values():
        j.pop("export_candles", None)
        j.pop("export_trades", None)
    gc.collect()
    return {"status": "cleared", "candles_freed": before}


@router.post("/api/telegram/test")
async def test_telegram(_: bool = Depends(require_admin)):
    if not telegram.bot:
        raise HTTPException(status_code=400, detail="Telegram not configured")
    if await telegram.send_test_message():
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed")
