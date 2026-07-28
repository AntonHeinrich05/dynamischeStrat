import asyncio, time, os, sys
import aiohttp
sys.path.insert(0, "/app/backend")
os.environ.setdefault("CANDLE_CACHE_DIR", "/tmp/cc_test")

from services import candle_cache as cc
from services.timeframes import aggregate_candles
from services import fast_sim
from services.backtester import simulate_pair
from strategies.custom_strategy import CustomStrategy

DEF = {
    "id": "t1", "name": "Test", "timeframe": "5m",
    "indicators": {"rsi_period": 14, "ema_fast_period": 9, "ema_slow_period": 50,
                   "atr_period": 14, "bb_period": 20, "bb_std": 2.0,
                   "macd_fast": 12, "macd_slow": 26, "macd_signal_period": 9},
    "long_rules": [{"indicator": "rsi", "op": "<", "value": 32},
                   {"indicator": "macd_hist", "op": ">", "value": 0}],
    "short_rules": [{"indicator": "rsi", "op": ">", "value": 68},
                    {"indicator": "ha_color", "op": "<", "value": 1}],
}
CFG = {"max_capital": 100, "leverage": 10, "fee_percent": 0.06, "tp1_crv": 1.0,
       "tp_full_crv": 2.0, "tp1_close_percent": 50, "sl_mode": "structure",
       "sl_lookback": 10, "atr_period": 14, "be_mode": "smart", "be_smart_lookback": 10}


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    async with aiohttp.ClientSession() as s:
        t = time.perf_counter()
        raw = await cc.get_candles(s, "BTCUSDT", days)
        print(f"download/load {len(raw)} 1m-Kerzen in {time.perf_counter()-t:.2f}s "
              f"({raw.nbytes()/1e6:.0f} MB)")
    t = time.perf_counter()
    c5 = aggregate_candles(raw, "5m")
    print(f"aggregate 5m -> {len(c5)} in {time.perf_counter()-t:.2f}s")
    t = time.perf_counter()
    fs = fast_sim.FastSeries(c5)
    prov = fast_sim.build_signal_provider(DEF, fs)
    print(f"FastSeries+provider in {time.perf_counter()-t:.2f}s")
    t = time.perf_counter()
    res = simulate_pair(CustomStrategy(DEF), c5, "BTCUSDT", {}, CFG, None, False, None, prov)
    print(f"simulate in {time.perf_counter()-t:.2f}s -> trades={res['trades']} "
          f"wr={res['win_rate']} pnl={res['pnl']}")
    # Referenzvergleich: gleiche Simulation auf Dict-Liste
    t = time.perf_counter()
    lst = c5.to_list()
    fs2 = fast_sim.FastSeries(lst)
    prov2 = fast_sim.build_signal_provider(DEF, fs2)
    res2 = simulate_pair(CustomStrategy(DEF), lst, "BTCUSDT", {}, CFG, None, False, None, prov2)
    print(f"REFERENZ (Dict-Liste) in {time.perf_counter()-t:.2f}s -> trades={res2['trades']} "
          f"wr={res2['win_rate']} pnl={res2['pnl']}")
    assert res["trades"] == res2["trades"], "Trade-Anzahl weicht ab!"
    assert abs(res["pnl"] - res2["pnl"]) < 0.01, "PnL weicht ab!"
    print("OK: identische Ergebnisse")

asyncio.run(main())
