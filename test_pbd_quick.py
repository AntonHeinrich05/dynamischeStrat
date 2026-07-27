"""Quick self-contained validation of the PBD Model strategy."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from strategies.registry import registry
from strategies.pbd_model_strategy import PBDModelStrategy


def c(ts, o, h, l, cl, v=1000):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": cl, "volume": v}


def build_bullish_pbd():
    """Construct a clean bullish Purge->Break->Displacement sequence."""
    candles = []
    ts = 0
    price = 100.0
    # 1) long downtrend then base to establish swing low ~ 95 and bias, plenty of history
    for i in range(120):
        # gentle uptrend base so HTF bias can turn long later; range around 98-102
        base = 98 + (i % 5) * 0.4
        candles.append(c(ts, base, base + 0.6, base - 0.6, base + 0.1)); ts += 1
    # establish a clear swing low at index ~125 (support = 96)
    for lo in [99, 98, 96, 98, 99]:
        candles.append(c(ts, lo + 0.5, lo + 1.0, lo - 0.2, lo + 0.6)); ts += 1
    # establish swing high ~ 103 to be broken (resistance)
    for hi in [100, 102, 103, 102, 100]:
        candles.append(c(ts, hi - 0.5, hi + 0.3, hi - 1.0, hi - 0.4)); ts += 1
    # drift back down toward support
    for p in [100, 99, 98, 97]:
        candles.append(c(ts, p, p + 0.4, p - 0.4, p - 0.2)); ts += 1
    # 2) PURGE: wick below support 96 then close back above (sell-side sweep)
    candles.append(c(ts, 97, 97.2, 94.5, 97.1)); ts += 1  # purge candle low=94.5 < 96
    # 3) BREAK + DISPLACEMENT: impulsive green candles creating FVG, close above 103
    candles.append(c(ts, 97.2, 99.0, 97.0, 98.9)); ts += 1   # c1
    candles.append(c(ts, 99.0, 104.5, 98.9, 104.2)); ts += 1  # c2 big displacement, closes above swing high 103
    candles.append(c(ts, 104.2, 105.0, 100.5, 104.8)); ts += 1  # c3 -> FVG bull: c1.high(99.0) < c3.low(100.5)
    # 4) retrace into FVG zone (99.0 - 100.5): current price inside
    candles.append(c(ts, 104.8, 105.0, 99.8, 100.0)); ts += 1  # retrace, close=100.0 inside FVG
    return candles


def main():
    strat = registry.get("pbd_model")
    assert strat is not None, "pbd_model not registered!"
    print("[OK] pbd_model registered:", strat.STRATEGY_NAME, "| tf:", strat.STRATEGY_TIMEFRAME)
    print("[OK] appears in list_all:", any(m["id"] == "pbd_model" for m in registry.list_all()))

    params = strat.get_params({}, "TESTUSDT")
    candles = build_bullish_pbd()
    res = strat.analyze(candles, "TESTUSDT", params)
    assert res is not None, "analyze returned None"
    ind = res["indicators"]
    print("\n--- PBD analyze() result ---")
    print("phase        :", ind["phase"])
    print("sweep        :", ind["sweep"], "| purge_level:", ind["purge_level"])
    print("mss_level    :", ind["mss_level"])
    print("fvg_zone     :", ind["fvg_zone"])
    print("confluence   :", ind["confluence"])
    print("bias         :", res["bias"], "| long_cnt:", res["long_count"], "short_cnt:", res["short_count"])
    print("signal_type  :", res["signal_type"], "| pre:", res["is_pre_signal"])
    print("levels       :", res["levels"])
    for r in res["rules"]:
        print(f"  rule {r['id']:<13} long={r['long']} short={r['short']}  ({r['label']})")

    # Assertions for a valid bullish A-setup
    assert ind["sweep"] == "sell_side", "expected sell-side purge"
    assert ind["mss_level"] is not None, "expected MSS detected"
    assert ind["fvg_zone"] is not None, "expected displacement FVG"
    assert ind["phase"] == "D", f"expected phase D, got {ind['phase']}"
    assert res["signal_type"] == "LONG", f"expected LONG signal, got {res['signal_type']}"
    assert res["is_pre_signal"] is False, "expected full signal, not pre"
    lv = res["levels"]
    assert lv["stop_loss"] < lv["entry"] < lv["take_profit_full"], "level ordering wrong"
    assert lv["crv"] >= 2.9, f"expected R:R ~>=3, got {lv['crv']}"

    # Duplicate protection: same setup should NOT re-fire as full signal
    res2 = strat.analyze(candles, "TESTUSDT", params)
    assert res2["is_pre_signal"] is True or res2["signal_type"] is None, \
        "duplicate setup should be suppressed (cooldown)"
    print("\n[OK] duplicate/cooldown protection: second identical setup ->",
          "pre" if res2["is_pre_signal"] else res2["signal_type"])

    print("\nALL PBD ASSERTIONS PASSED ✔")


if __name__ == "__main__":
    main()
