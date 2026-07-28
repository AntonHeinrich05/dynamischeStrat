"""Timeframe-Aggregation: 1m-Kerzen -> höhere Timeframes."""
from typing import Dict, List

import numpy as np

from services.candles import CandleArray

TIMEFRAMES: Dict[str, int] = {
    "1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "24h": 1440, "3d": 4320, "1w": 10080, "1M": 43200,
}

TIMEFRAME_ORDER = list(TIMEFRAMES.keys())


def _aggregate_array(ca: CandleArray, bucket_ms: int, drop_partial: bool) -> CandleArray:
    """Vektorisierte Aggregation – bei Millionen Kerzen ~100x schneller als
    die Schleife und ohne Zwischen-Dicts."""
    n = len(ca)
    if n == 0:
        return ca
    keys = ca.ts // bucket_ms
    starts = np.concatenate([[0], np.flatnonzero(keys[1:] != keys[:-1]) + 1])
    ends = np.concatenate([starts[1:], [n]])
    out = CandleArray(
        keys[starts] * bucket_ms,
        ca.op[starts],
        np.maximum.reduceat(ca.hi, starts),
        np.minimum.reduceat(ca.lo, starts),
        ca.cl[ends - 1],
        np.add.reduceat(ca.vol, starts),
    )
    if drop_partial and len(out):
        last_close_ms = int(ca.ts[-1]) + 60000
        if last_close_ms < int(out.ts[-1]) + bucket_ms:
            out = out[:-1]
    return out


def aggregate_candles(candles, timeframe: str, drop_partial: bool = False):
    minutes = TIMEFRAMES.get(timeframe, 1)
    if minutes <= 1 or not candles:
        return candles
    bucket_ms = minutes * 60000
    if isinstance(candles, CandleArray):
        return _aggregate_array(candles, bucket_ms, drop_partial)
    out: List[Dict] = []
    cur_key = None
    bucket = None
    for c in candles:
        key = c["timestamp"] // bucket_ms
        if key != cur_key:
            if bucket is not None:
                out.append(bucket)
            bucket = {"timestamp": key * bucket_ms, "open": c["open"], "high": c["high"],
                      "low": c["low"], "close": c["close"], "volume": c.get("volume", 0.0)}
            cur_key = key
        else:
            bucket["high"] = max(bucket["high"], c["high"])
            bucket["low"] = min(bucket["low"], c["low"])
            bucket["close"] = c["close"]
            bucket["volume"] += c.get("volume", 0.0)
    if bucket is not None:
        if drop_partial:
            last_close_ms = candles[-1]["timestamp"] + 60000
            if last_close_ms >= bucket["timestamp"] + bucket_ms:
                out.append(bucket)
        else:
            out.append(bucket)
    return out
