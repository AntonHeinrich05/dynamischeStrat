import asyncio, sys, time, aiohttp
sys.path.insert(0, '/app/backend')
from services.backtester import fetch_history, simulate_pair, make_session_checker
from services.bitunix_trade import DEFAULT_COIN_CFG
from services import fast_sim
from strategies.custom_strategy import CustomStrategy

DEF = {"id": "t1", "name": "T", "indicators": {},
       "long_rules": [{"indicator": "rsi", "op": "<", "value": 48},
                      {"indicator": "price", "op": ">", "value": "ema_slow"},
                      {"indicator": "rel_volume", "op": ">", "value": 0.8}],
       "short_rules": [{"indicator": "rsi", "op": ">", "value": 52},
                       {"indicator": "price", "op": "<", "value": "ema_slow"},
                       {"indicator": "rel_volume", "op": ">", "value": 0.8}]}

async def main():
    async with aiohttp.ClientSession() as s:
        candles = await fetch_history(s, "BTCUSDT", 3)
    print("candles:", len(candles))
    strat = CustomStrategy(DEF)
    cfg = dict(DEFAULT_COIN_CFG)
    t0 = time.time()
    slow = simulate_pair(strat, candles, "BTCUSDT", {}, cfg)
    t1 = time.time()
    fs = fast_sim.FastSeries(candles)
    prov = fast_sim.build_signal_provider(DEF, fs)
    fast = simulate_pair(strat, candles, "BTCUSDT", {}, cfg, signal_provider=prov)
    t2 = time.time()
    print(f"SLOW: {slow['trades']} trades pnl {slow['pnl']} in {t1-t0:.2f}s")
    print(f"FAST: {fast['trades']} trades pnl {fast['pnl']} in {t2-t1:.2f}s  (speedup {round((t1-t0)/max(t2-t1,0.001))}x)")
    # sessions test
    sess = simulate_pair(strat, candles, "BTCUSDT", {}, {**cfg, "sessions": "09:00-12:00"}, signal_provider=prov)
    print(f"SESSION 09-12: {sess['trades']} trades (should be < {fast['trades']})")
    # BE modes
    for m in ["off", "tp1", "crv", "profit_pct", "smart"]:
        r = simulate_pair(strat, candles, "BTCUSDT", {}, {**cfg, "be_mode": m, "be_trigger_crv": 0.5}, signal_provider=prov)
        print(f"BE {m}: {r['trades']} trades pnl {r['pnl']} be_moved {r['be_moved']}")
    # profit secure visible
    r = simulate_pair(strat, candles, "BTCUSDT", {}, {**cfg, "profit_secure_enabled": True, "profit_secure_trigger_pct": 10}, signal_provider=prov)
    print(f"PS: secured {r['secured']} / {r['trades']}")

asyncio.run(main())
