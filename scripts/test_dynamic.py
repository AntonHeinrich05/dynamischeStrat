import asyncio, os, sys, time, json
sys.path.insert(0, "/app/backend")
os.environ.setdefault("CANDLE_CACHE_DIR", "/tmp/cc_test")

from services import optimizer as opt
from services.bitunix_trade import DEFAULT_COIN_CFG
from strategies.registry import registry

DEF = {
    "id": "custom_test_dyn", "name": "TestBasis", "timeframe": "5m",
    "indicators": {}, "long_rules": [{"indicator": "rsi", "op": "<", "value": 32}],
    "short_rules": [{"indicator": "rsi", "op": ">", "value": 68}],
}


async def main():
    registry.load_custom([DEF])
    body = {
        "mode": "dynamic", "strategy_id": "custom_test_dyn",
        "symbols": ["BTCUSDT"], "days": int(sys.argv[1]) if len(sys.argv) > 1 else 120,
        "timeframe": "5m", "objective": "balanced", "iterations": 6,
        "min_trades": 10, "indicators": ["rsi", "ema_fast", "ema_slow", "macd_hist",
                                         "bb_lower", "ha_color", "rel_volume"],
        "dynamic": {"max_regimes": 4, "lookback_days": 3, "confidence_min": 70,
                    "min_hold_days": 2, "min_share_pct": 5,
                    "per_regime_strategies": True, "max_rules_per_regime": 2},
    }
    job_id = opt.create_job(body)
    t = time.perf_counter()
    await opt.run_optimizer(job_id, body, registry, {}, DEFAULT_COIN_CFG, None)
    job = opt.JOBS[job_id]
    print(f"status={job['status']} in {time.perf_counter()-t:.1f}s err={job['error']}")
    res = job.get("result") or {}
    dyn = res.get("dynamic") or {}
    print("regime count:", len(dyn.get("regimes") or []))
    for r in dyn.get("regimes") or []:
        print(f"  R{r['regime']} {r['label']} share={r['share_pct']}% "
              f"trades={r['metrics']['trades']} pnl={r['metrics']['pnl']} "
              f"own={r.get('own_strategy')}")
    print("verdict:", json.dumps(dyn.get("verdict"), ensure_ascii=False)[:400])
    print("comparison:", json.dumps(dyn.get("comparison"), ensure_ascii=False)[:400])

asyncio.run(main())
