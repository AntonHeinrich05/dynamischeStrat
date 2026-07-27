"""Hintergrund-Loops: Scanner-Polling & täglicher Reset.
(1:1 aus server.py verschoben – app.mongodb -> state.db)"""
import asyncio
import logging
from datetime import datetime, timezone

from core import state
from core.config import ALL_SYMBOLS, OTHER_YAHOO, POLL_INTERVAL
from core.state import scanner, feed, autotrader, scanner_running, open_signal_evals
from core.pipeline import process_signal, evaluate_open_signals, broadcast

logger = logging.getLogger(__name__)

_last_reset_date = None


async def start_scanner():
    logger.info(f"Scanner started for {len(ALL_SYMBOLS)} instruments (every {POLL_INTERVAL}s)")
    scanner_running.set()
    while scanner_running.is_set():
        prices = {}
        for symbol in ALL_SYMBOLS:
            if not scanner_running.is_set():
                break
            try:
                if symbol in OTHER_YAHOO:
                    klines = await feed.fetch_commodity(OTHER_YAHOO[symbol], "1d")
                else:
                    klines = await feed.fetch(symbol, 5)
                if len(klines) < 2:
                    continue
                closed_candles = klines[:-1]
                forming = klines[-1]
                new_candle = False
                for candle in closed_candles[-3:]:
                    if scanner.add_closed_candle(symbol, candle):
                        new_candle = True
                scanner.forming[symbol] = forming
                price = forming["close"]
                prices[symbol] = price

                signals = scanner.analyze_symbol(symbol)
                if new_candle:
                    for sig in signals:
                        await process_signal(sig, scanner.candle_buffer.get(symbol, []))

                await evaluate_open_signals(symbol, price)
                await broadcast({"type": "candle", "symbol": symbol, "data": forming})
                states = scanner.rule_states.get(symbol)
                if states:
                    await broadcast({"type": "rule_states", "symbol": symbol, "data": states})
            except Exception as e:
                logger.error(f"Scan error for {symbol}: {e}")
            await asyncio.sleep(0.1)
        try:
            await autotrader.monitor(prices)
        except Exception as e:
            logger.error(f"autotrade monitor error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


async def daily_reset_loop():
    """At Berlin midnight: aggregate the day into compact analytics.
    (Raw signals & closed trades are NOT deleted anymore – auf Nutzerwunsch.)"""
    global _last_reset_date
    _last_reset_date = scanner.berlin_date()
    while True:
        await asyncio.sleep(60)
        today = scanner.berlin_date()
        if today != _last_reset_date:
            await perform_daily_reset(_last_reset_date)
            _last_reset_date = today


async def perform_daily_reset(prev_date: str):
    logger.info(f"Daily reset for {prev_date}")
    try:
        pipeline = [
            {"$match": {"trade_date": prev_date}},
            {"$group": {"_id": {"strategy": "$strategy_id", "type": "$type"},
                        "total": {"$sum": 1},
                        "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                        "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                        "avg_crv": {"$avg": "$crv"}}},
        ]
        rows = await state.db.signals.aggregate(pipeline).to_list(500)
        summary = {"date": prev_date, "generated_at": datetime.now(timezone.utc).isoformat(),
                   "by_strategy_type": [{"strategy": r["_id"]["strategy"], "type": r["_id"]["type"],
                                         "total": r["total"], "wins": r["wins"], "losses": r["losses"],
                                         "avg_crv": round(r.get("avg_crv") or 0, 2)} for r in rows]}
        total = sum(r["total"] for r in rows)
        summary["total_signals"] = total
        await state.db.analytics_daily.update_one({"date": prev_date}, {"$set": summary}, upsert=True)
        # trade stats aggregate
        tstats = await state.db.auto_trades.aggregate([
            {"$match": {"trade_date": prev_date, "status": "closed"}},
            {"$group": {"_id": None, "trades": {"$sum": 1},
                        "pnl": {"$sum": "$realized_pnl"},
                        "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}}}}],
        ).to_list(1)
        if tstats:
            ts = tstats[0]
            await state.db.trade_stats.update_one({"date": prev_date}, {"$set": {
                "date": prev_date, "trades": ts["trades"], "pnl": round(ts.get("pnl") or 0, 4),
                "wins": ts["wins"]}}, upsert=True)
        # NOTE: Auto-Löschung deaktiviert – Signale und geschlossene Trades
        # bleiben dauerhaft in der DB erhalten (auf Nutzerwunsch).
        open_signal_evals.clear()
        await broadcast({"type": "daily_reset", "date": prev_date})
    except Exception as e:
        logger.error(f"daily reset error: {e}")
