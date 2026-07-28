"""
Memory-Profiling für 90/180/360-Tage 1min-Backtests.
Simuliert den Render Free-Tier-Fall (512 MB RAM / 0.1 CPU).
"""
import asyncio, gc, os, resource, sys, time, aiohttp, tracemalloc
sys.path.insert(0, '/app/backend')
from services.backtester import fetch_history, simulate_pair
from services.bitunix_trade import DEFAULT_COIN_CFG
from services import fast_sim, candle_cache
from strategies.registry import registry as _registry

DAYS_TO_TEST = int(os.environ.get("BENCH_DAYS", "30"))  # Default klein, für CI


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def main():
    candle_cache.clear()
    gc.collect()
    tracemalloc.start()
    peak0 = rss_mb()
    print(f"[baseline] RSS={peak0:.1f} MB")

    async with aiohttp.ClientSession() as s:
        t0 = time.time()
        candles = await fetch_history(s, "BTCUSDT", DAYS_TO_TEST)
        t1 = time.time()
    print(f"[load] {DAYS_TO_TEST}d 1m -> {len(candles)} candles in {t1-t0:.1f}s | RSS={rss_mb():.1f} MB")

    fs = fast_sim.FastSeries(candles)
    print(f"[fs] FastSeries built | RSS={rss_mb():.1f} MB")

    cfg = dict(DEFAULT_COIN_CFG)
    for sid in ["rsi_only", "macd_rsi_momentum", "scalping_4_rules"]:
        strat = _registry.get(sid)
        if not strat:
            continue
        t0 = time.time()
        prov = fast_sim.build_builtin_signal_provider(strat, fs, {}, "BTCUSDT")
        res = simulate_pair(strat, candles, "BTCUSDT", {}, cfg, signal_provider=prov)
        t1 = time.time()
        print(f"[sim] {sid}: trades={res['trades']} pnl={res['pnl']} "
              f"in {t1-t0:.2f}s | RSS={rss_mb():.1f} MB")

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"[peak] Python-Heap peak={peak/1e6:.1f} MB | Process RSS peak={rss_mb():.1f} MB")


asyncio.run(main())
