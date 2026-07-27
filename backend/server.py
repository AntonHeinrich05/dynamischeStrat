"""Crypto Scalping Scanner – App-Assembly.

Die gesamte Endpoint-Logik liegt in `routers/` (ein Modul pro Bereich),
geteilter Zustand in `core/state.py`, Hintergrund-Loops in `core/scheduler.py`.
Neue Bereiche: Router in routers/ anlegen und in routers/__init__.py registrieren.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from core import state
from core.config import TOP_10_COINS, OTHER_INSTRUMENTS, ALL_SYMBOLS
from core.state import scanner, telegram, feed, trade_client, autotrader, \
    strategy_coin_toggles, toggle_enabled
from core.pipeline import emit_ai_signal
from core.scheduler import start_scanner, daily_reset_loop
from services.ai_engine import ai_engine
from strategies.registry import registry as strategy_registry
from routers import ALL_ROUTERS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Crypto Scalping Scanner...")
    app.mongodb_client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    app.mongodb = app.mongodb_client[os.getenv("DB_NAME", "crypto_scanner")]
    state.db = app.mongodb
    autotrader.set_db(app.mongodb)
    autotrader.set_telegram(telegram)
    # Load Bitunix contract catalogue so the symbol mapping is validated
    # against the real contract list and qty/price step sizes are known.
    try:
        await trade_client.load_trading_pairs()
    except Exception as e:
        logger.error(f"Bitunix trading_pairs load failed: {e}")
    logger.info("Connected to MongoDB")

    saved = await app.mongodb.settings.find_one({"_id": "scanner_settings"})
    if saved:
        saved.pop("_id", None)
        scanner.update_settings(saved)
    else:
        await app.mongodb.settings.insert_one({"_id": "scanner_settings", **scanner.settings})

    # load custom strategies
    customs = await app.mongodb.custom_strategies.find().to_list(100)
    for c in customs:
        c.pop("_id", None)
    strategy_registry.load_custom(customs)

    # ---- KI Trader engine ----
    ai_engine.setup(db=app.mongodb, scanner=scanner, signal_cb=emit_ai_signal,
                    toggle_check=toggle_enabled, symbols=list(ALL_SYMBOLS))
    await ai_engine.load_config()
    _ens = list(scanner.settings.get("enabled_strategies") or [])
    if "ai_trader" not in _ens and "ai_trader" not in scanner.settings.get("deleted_strategies", []):
        _ens.append("ai_trader")
        scanner.settings["enabled_strategies"] = _ens
        await app.mongodb.settings.update_one(
            {"_id": "scanner_settings"},
            {"$set": {"enabled_strategies": _ens}}, upsert=True)

    # load autotrade config
    at_cfg = await app.mongodb.settings.find_one({"_id": "autotrade_config"})
    if at_cfg:
        at_cfg.pop("_id", None)
        autotrader.set_config(at_cfg)
    else:
        cfg = {"mode": os.getenv("TRADING_MODE", "paper"), "coins": {}, "strategy_overrides": {}}
        await app.mongodb.settings.insert_one({"_id": "autotrade_config", **cfg})
        autotrader.set_config(cfg)

    # ---- Load persisted capital allocation (live/paper getrennt) ----
    cap_doc = await app.mongodb.settings.find_one({"_id": "capital_allocation"})
    if cap_doc:
        cap_doc.pop("_id", None)
        autotrader.config["capital_allocation"] = cap_doc

    # ---- Load strategy_coin_configs from dedicated collection ----
    # Without this, the per-strategy-per-coin paper/live mode lives only in the
    # DB and the in-memory autotrader never sees it -> it falls back to the
    # global/strategy mode and can fire REAL live orders even though the UI
    # shows the pair as "paper". Loading them here fixes that.
    try:
        scc_docs = await app.mongodb.strategy_coin_configs.find().to_list(2000)
        scc_map = {}
        for d in scc_docs:
            key = d.get("_id")
            if key:
                scc_map[key] = d.get("config", {})
        if scc_map:
            autotrader.config.setdefault("strategy_coin_configs", {}).update(scc_map)
            logger.info(f"Loaded {len(scc_map)} strategy_coin_configs from DB")
    except Exception as e:
        logger.warning(f"Loading strategy_coin_configs failed: {e}")

    # load admin control toggles (stop trades / stop signals)
    ctrl = await app.mongodb.settings.find_one({"_id": "control_state"})
    if ctrl:
        state.control_state["trades_paused"] = bool(ctrl.get("trades_paused", False))
        state.control_state["signals_paused"] = bool(ctrl.get("signals_paused", False))
    else:
        await app.mongodb.settings.insert_one({"_id": "control_state", **state.control_state})

    # ---- strategy_coin_toggles: index + migration + in-memory cache ----
    try:
        await app.mongodb.strategy_coin_toggles.create_index(
            [("strategy_id", 1), ("symbol", 1)], unique=True
        )
    except Exception as e:
        logger.warning(f"strategy_coin_toggles index setup: {e}")

    # Migration: seed enabled=True for every (existing strategy, symbol) combo
    # that has no record yet. Missing rows already default to enabled=True via
    # `toggle_enabled`, so this is idempotent and non-destructive.
    all_strategy_ids = [m["id"] for m in strategy_registry.list_all()]
    deleted_strats = set(scanner.settings.get("deleted_strategies", []))
    all_strategy_ids = [sid for sid in all_strategy_ids if sid not in deleted_strats]
    now_iso = datetime.now(timezone.utc).isoformat()
    if all_strategy_ids:
        migration_ops = []
        for sid in all_strategy_ids:
            for sym in ALL_SYMBOLS:
                migration_ops.append({
                    "filter": {"strategy_id": sid, "symbol": sym},
                    "update": {"$setOnInsert": {
                        "strategy_id": sid, "symbol": sym,
                        "enabled": True, "updated_at": now_iso,
                    }},
                })
        for op in migration_ops:
            try:
                await app.mongodb.strategy_coin_toggles.update_one(
                    op["filter"], op["update"], upsert=True
                )
            except Exception as e:
                logger.debug(f"toggle migration skip {op['filter']}: {e}")

    # Load toggles into cache
    async for row in app.mongodb.strategy_coin_toggles.find({}):
        strategy_coin_toggles[(row.get("strategy_id"), row.get("symbol"))] = \
            bool(row.get("enabled", True))
    logger.info(f"Loaded {len(strategy_coin_toggles)} strategy_coin_toggles")

    logger.info("Probing market data sources...")
    await feed.probe("BTCUSDT")
    # Bei höheren Timeframes mehr 1m-Historie laden, damit die Strategien
    # direkt nach dem Start genug aggregierte Kerzen haben.
    need = scanner.buffer_limit()
    if need > 900:
        from services import backtester as _bt
        import aiohttp as _aiohttp
        days_needed = min(14, need // 1440 + 1)
        async with _aiohttp.ClientSession() as _session:
            for symbol in TOP_10_COINS:
                try:
                    hist = await _bt.fetch_history(_session, symbol, days_needed)
                    scanner.bootstrap(symbol, hist[:-1] if len(hist) > 1 else hist)
                except Exception as e:
                    logger.error(f"Extended bootstrap failed for {symbol}: {e}")
                await asyncio.sleep(0.15)
    else:
        for symbol in TOP_10_COINS:
            try:
                hist = await feed.fetch(symbol, 200)
                scanner.bootstrap(symbol, hist[:-1] if len(hist) > 1 else hist)
            except Exception as e:
                logger.error(f"Bootstrap failed for {symbol}: {e}")
            await asyncio.sleep(0.15)
    for inst in OTHER_INSTRUMENTS:
        try:
            hist = await feed.fetch_commodity(inst["yahoo"], "5d")
            closed = hist[:-1] if len(hist) > 1 else hist
            scanner.bootstrap(inst["symbol"], closed[-200:])
        except Exception as e:
            logger.error(f"Bootstrap failed for {inst['symbol']}: {e}")
        await asyncio.sleep(0.15)

    asyncio.create_task(start_scanner())
    asyncio.create_task(daily_reset_loop())
    asyncio.create_task(ai_engine.run_loop())

    # BUGFIX (win-rate): re-hydrate the in-memory open_signal_evals from today's
    # still-open signals so evaluate_open_signals() can mark them as win/loss
    # after a restart. Without this any signal that emitted before the restart
    # never got its `result` filled in.
    try:
        today = scanner.berlin_date()
        cursor = app.mongodb.signals.find({
            "trade_date": today,
            "signal_class": {"$ne": "PRE_SIGNAL"},
            "$or": [{"result": {"$exists": False}}, {"result": None}],
        })
        rehydrated = 0
        async for s in cursor:
            tp1 = s.get("tp1") or s.get("take_profit_1")
            sl = s.get("sl") or s.get("stop_loss")
            if not tp1 or not sl:
                continue
            state.open_signal_evals.append({
                "id": s.get("id"),
                "symbol": s.get("symbol"),
                "type": s.get("type"),
                "tp1": tp1,
                "sl": sl,
                "strategy_id": s.get("strategy_id", "unknown"),
            })
            rehydrated += 1
        if rehydrated:
            logger.info(f"Re-hydrated {rehydrated} open signal evaluations from DB")
    except Exception as e:
        logger.warning(f"open_signal_evals rehydration failed: {e}")

    # initial analyze so rule-states are populated immediately
    for symbol in ALL_SYMBOLS:
        try:
            scanner.analyze_symbol(symbol)
        except Exception:
            pass
    if telegram.bot:
        await telegram.send_test_message()
    yield
    logger.info("Shutting down...")
    state.scanner_running.clear()
    await feed.close()
    app.mongodb_client.close()


app = FastAPI(title="Crypto Scalping Scanner", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

for r in ALL_ROUTERS:
    app.include_router(r)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
