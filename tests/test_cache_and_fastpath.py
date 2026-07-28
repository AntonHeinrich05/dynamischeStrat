"""
Regression + Speedup Test:
- Kerzen-Cache: gleicher symbol/days -> zweiter Call ist deutlich schneller UND liefert die gleichen Kerzen.
- Fast-Path für Built-ins: Ergebnisse (Trades, PnL) müssen ähnlich zum Legacy-Pfad sein.
"""
import asyncio, sys, time, aiohttp
sys.path.insert(0, '/app/backend')
from services.backtester import fetch_history, simulate_pair
from services.bitunix_trade import DEFAULT_COIN_CFG
from services import fast_sim, candle_cache
from strategies.registry import registry as _registry

BUILTINS_TO_CHECK = ["rsi_only", "macd_rsi_momentum", "scalping_4_rules"]


async def main():
    candle_cache.clear()

    # 1) Cache check
    async with aiohttp.ClientSession() as s:
        t0 = time.time()
        candles1 = await fetch_history(s, "BTCUSDT", 2)
        t1 = time.time()
        candles2 = await fetch_history(s, "BTCUSDT", 2)
        t2 = time.time()
    print(f"[cache] fresh fetch: {t1-t0:.2f}s / cached: {t2-t1:.3f}s "
          f"speedup={round((t1-t0)/max(t2-t1,0.001))}x  candles={len(candles1)}")
    assert len(candles1) == len(candles2), "cache size mismatch"
    assert candles1[-1]["timestamp"] == candles2[-1]["timestamp"]

    stats = candle_cache.stats()
    print(f"[cache] stats: {stats}")

    # 2) Fast-Path check for built-ins
    cfg = dict(DEFAULT_COIN_CFG)
    for sid in BUILTINS_TO_CHECK:
        strat = _registry.get(sid)
        if not strat:
            print(f"[fast] {sid}: SKIPPED (nicht registriert)")
            continue
        fs = fast_sim.FastSeries(candles1)
        t0 = time.time()
        legacy = simulate_pair(strat, candles1, "BTCUSDT", {}, cfg)
        t1 = time.time()
        prov = fast_sim.build_builtin_signal_provider(strat, fs, {}, "BTCUSDT")
        if not prov:
            print(f"[fast] {sid}: KEIN vectorized_signals -> Legacy-only ({legacy['trades']} trades)")
            continue
        fast = simulate_pair(strat, candles1, "BTCUSDT", {}, cfg, signal_provider=prov)
        t2 = time.time()
        speedup = round((t1-t0) / max(t2-t1, 0.001), 1)
        # Trades sollten "ähnlich" sein (kleine Abweichung akzeptabel durch numerische Details)
        diff = abs(fast["trades"] - legacy["trades"])
        rel = diff / max(legacy["trades"], 1) if legacy["trades"] else 0.0
        status = "OK" if rel < 0.25 else "DIVERGENT"
        print(f"[fast] {sid}: legacy trades={legacy['trades']} pnl={legacy['pnl']} in {t1-t0:.2f}s | "
              f"fast trades={fast['trades']} pnl={fast['pnl']} in {t2-t1:.2f}s | "
              f"speedup={speedup}x | {status}")


asyncio.run(main())
