"""Autotrade-Endpoints: Konfiguration, Trades, Kapital, Balance."""
import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.defaults import DEFAULT_STRATEGY_OVERRIDE, DEFAULT_STRATEGY_COIN_CFG
from core.state import scanner, autotrader, trade_client
from core.utils import _enrich_trade
from services.bitunix_trade import DEFAULT_COIN_CFG

logger = logging.getLogger(__name__)

router = APIRouter(tags=["autotrade"])


@router.get("/api/autotrade/config")
async def get_autotrade_config():
    return {"config": autotrader.config, "defaults": DEFAULT_COIN_CFG,
            "bitunix_configured": trade_client.configured(),
            "strategy_overrides": autotrader.config.get("strategy_overrides", {})}


@router.post("/api/autotrade/config")
async def set_autotrade_config(config: Dict, _: bool = Depends(require_admin)):
    if "mode" not in config:
        config["mode"] = autotrader.config.get("mode", "paper")
    config.setdefault("coins", autotrader.config.get("coins", {}))
    config.setdefault("strategy_overrides", autotrader.config.get("strategy_overrides", {}))
    autotrader.set_config(config)
    await state.db.settings.update_one({"_id": "autotrade_config"},
                                       {"$set": {"mode": config["mode"], "coins": config["coins"],
                                                 "strategy_overrides": config.get("strategy_overrides", {})}},
                                       upsert=True)
    return {"status": "success", "config": autotrader.config}


@router.post("/api/autotrade/coin/{symbol}")
async def set_coin_config(symbol: str, cfg: Dict, _: bool = Depends(require_admin)):
    coins = dict(autotrader.config.get("coins", {}))
    merged = dict(DEFAULT_COIN_CFG)
    merged.update(coins.get(symbol, {}))
    merged.update(cfg)
    coins[symbol] = merged
    new_cfg = {"mode": autotrader.config.get("mode", "paper"), "coins": coins,
               "strategy_overrides": autotrader.config.get("strategy_overrides", {})}
    autotrader.set_config(new_cfg)
    await state.db.settings.update_one({"_id": "autotrade_config"},
                                       {"$set": {"mode": new_cfg["mode"], "coins": coins,
                                                 "strategy_overrides": new_cfg.get("strategy_overrides", {})}}, upsert=True)
    return {"status": "success", "coin": symbol, "config": merged}


@router.post("/api/autotrade/strategy/{strategy_id}")
async def set_strategy_autotrade(strategy_id: str, cfg: Dict, _: bool = Depends(require_admin)):
    """Set auto-trade configuration for a specific strategy.
    This overrides the global mode and coin-level settings when this strategy fires."""
    overrides = dict(autotrader.config.get("strategy_overrides", {}))
    current = overrides.get(strategy_id, dict(DEFAULT_STRATEGY_OVERRIDE))
    current.update(cfg)
    overrides[strategy_id] = current

    new_cfg = {
        "mode": autotrader.config.get("mode", "paper"),
        "coins": autotrader.config.get("coins", {}),
        "strategy_overrides": overrides
    }
    autotrader.set_config(new_cfg)
    await state.db.settings.update_one(
        {"_id": "autotrade_config"},
        {"$set": {"mode": new_cfg["mode"], "coins": new_cfg["coins"],
                  "strategy_overrides": overrides}},
        upsert=True
    )
    return {"status": "success", "strategy_id": strategy_id, "config": current}


@router.get("/api/autotrade/strategy/{strategy_id}")
async def get_strategy_autotrade(strategy_id: str):
    """Get auto-trade configuration for a specific strategy."""
    overrides = autotrader.config.get("strategy_overrides", {})
    cfg = overrides.get(strategy_id, dict(DEFAULT_STRATEGY_OVERRIDE))
    return {"strategy_id": strategy_id, "config": cfg, "defaults": DEFAULT_STRATEGY_OVERRIDE}


@router.get("/api/autotrade/strategy/{strategy_id}/coin/{symbol}")
async def get_strategy_coin_autotrade(
    strategy_id: str,
    symbol: str,
):
    doc = await state.db.strategy_coin_configs.find_one({"_id": f"{strategy_id}_{symbol}"})
    saved = doc.get("config", {}) if doc else {}
    merged = {**DEFAULT_STRATEGY_COIN_CFG, **saved}
    return {"config": merged}


@router.post("/api/autotrade/strategy/{strategy_id}/coin/{symbol}")
async def set_strategy_coin_autotrade(
    strategy_id: str,
    symbol: str,
    body: dict,
    _=Depends(require_admin)
):
    key = f"{strategy_id}_{symbol}"
    await state.db.strategy_coin_configs.replace_one(
        {"_id": key},
        {"_id": key, "config": body},
        upsert=True
    )
    # Sync to in-memory autotrader config
    autotrader.config.setdefault("strategy_coin_configs", {})[key] = body
    logger.info(f"[AutoTrade] Per-coin config saved: strategy={strategy_id} coin={symbol} mode={body.get('mode')}")
    return {"ok": True}


@router.get("/api/autotrade/strategy_coin_configs")
async def list_strategy_coin_autotrade(_=Depends(require_admin)):
    """Return ALL per-strategy per-coin auto-trade configs as a nested dict:
        { strategy_id: { symbol: { mode, enabled, ... } } }
    Used by the frontend to reflect the active mode on the strategy blitz icon.
    """
    docs = await state.db.strategy_coin_configs.find().to_list(2000)
    out: Dict[str, Dict[str, Dict]] = {}
    for d in docs:
        key = d.get("_id") or ""
        if "_" not in key:
            continue
        # split on the LAST underscore so strategy ids with underscores still work
        strategy_id, symbol = key.rsplit("_", 1)
        out.setdefault(strategy_id, {})[symbol] = d.get("config", {})
    return {"configs": out}


@router.get("/api/autotrade/trades")
async def get_trades(status: str = None, limit: int = 50, mode: str = None):
    q = {}
    if status:
        q["status"] = status
    if mode in ("live", "paper"):
        q["mode"] = mode
    trades = await state.db.auto_trades.find(q).sort("opened_at", -1).limit(limit).to_list(limit)
    return {"trades": [
        _enrich_trade(t, scanner.current_price(t["symbol"]) if t.get("status") == "open" else None)
        for t in trades
    ]}


@router.get("/api/autotrade/trades/{trade_id}")
async def get_trade_detail(trade_id: str):
    t = await state.db.auto_trades.find_one({"id": trade_id})
    if not t:
        raise HTTPException(status_code=404, detail="Trade not found")
    cur = scanner.current_price(t["symbol"]) if t.get("status") == "open" else None
    return {"trade": _enrich_trade(t, cur)}


@router.post("/api/autotrade/close/{trade_id}")
async def close_trade(trade_id: str, _: bool = Depends(require_admin)):
    t = await state.db.auto_trades.find_one({"id": trade_id})
    if not t:
        raise HTTPException(status_code=404, detail="Trade not found")
    price = scanner.current_price(t["symbol"]) or t["entry"]
    res = await autotrader.manual_close(trade_id, price)
    return {"status": "success", "result": res}


@router.get("/api/autotrade/capital")
async def get_capital_allocation():
    """Kapital-Zuweisung für Live & Paper inkl. aktuell zugewiesenem/freiem Kapital."""
    total = await autotrader._live_total_balance()
    out = {}
    for scope in ("live", "paper"):
        a = autotrader.capital_allocation(scope)
        allocated = await autotrader.allocated_capital(
            scope, total=total if scope == "live" else None)
        used = await autotrader.used_margin(scope)
        out[scope] = {
            **a,
            "allocated": round(allocated, 2) if allocated is not None else None,
            "used_margin": round(used, 2),
            "free": round(allocated - used, 2) if allocated is not None else None,
        }
    return {"allocation": out,
            "live_total_balance": round(total, 2) if total is not None else None,
            "bitunix_configured": trade_client.configured()}


@router.post("/api/autotrade/capital")
async def set_capital_allocation(body: Dict, _: bool = Depends(require_admin)):
    """Kapital-Zuweisung speichern: scope=live|paper, mode=full|fixed|percent, value."""
    scope = body.get("scope")
    if scope not in ("live", "paper"):
        raise HTTPException(status_code=400, detail="scope muss live|paper sein")
    mode = body.get("mode")
    if mode not in ("full", "fixed", "percent"):
        raise HTTPException(status_code=400, detail="mode muss full|fixed|percent sein")
    try:
        value = float(body.get("value") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Ungültiger Wert")
    if mode == "fixed" and value <= 0:
        raise HTTPException(status_code=400, detail="Fester Betrag muss größer als 0 sein")
    if mode == "percent" and not (0 < value <= 100):
        raise HTTPException(status_code=400, detail="Prozentsatz muss zwischen 1 und 100 liegen")
    if mode == "fixed" and scope == "live":
        total = await autotrader._live_total_balance()
        if total is not None and value > total:
            raise HTTPException(
                status_code=400,
                detail=f"Fester Betrag ({value:.2f} USDT) übersteigt das Gesamtguthaben ({total:.2f} USDT)")
    alloc = dict(autotrader.config.get("capital_allocation", {}) or {})
    entry = dict(alloc.get(scope, {}))
    entry.update({"mode": mode, "value": value})
    if scope == "paper" and body.get("base_balance") is not None:
        try:
            bb = float(body["base_balance"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Ungültiges Simulations-Guthaben")
        if bb <= 0:
            raise HTTPException(status_code=400, detail="Simulations-Guthaben muss größer als 0 sein")
        entry["base_balance"] = bb
    alloc[scope] = entry
    autotrader.config["capital_allocation"] = alloc
    await state.db.settings.update_one({"_id": "capital_allocation"},
                                       {"$set": alloc}, upsert=True)
    logger.info(f"[Capital] Allocation saved: {scope} -> {entry}")
    return {"status": "success", "allocation": alloc}


@router.get("/api/autotrade/balance")
async def get_balance():
    # Current mode (live or paper)
    mode = autotrader.config.get("mode", "paper")

    # ---- Primary mode stats (live or paper) ----
    open_ct = await state.db.auto_trades.count_documents({"status": "open"})
    closed = await state.db.auto_trades.find({"status": "closed"}).to_list(1000)
    pnl = round(sum(t.get("realized_pnl", 0) for t in closed), 4)

    result = {
        "mode": mode,
        "realized_pnl": pnl,
        "open_trades": open_ct,
        "closed_trades": len(closed),
        "bitunix_configured": trade_client.configured(),
    }

    # ---- Live mode: fetch Bitunix balance ----
    if trade_client.configured():

        try:
            bal = await trade_client.get_balance()
            data = bal.get("data") if isinstance(bal, dict) else None
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                def _num(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return 0.0
                available = _num(data.get("available") or data.get("availableBalance"))
                frozen = _num(data.get("frozen"))
                used_margin = _num(data.get("margin"))
                upnl = _num(data.get("crossUnrealizedPNL")) + _num(data.get("isolationUnrealizedPNL"))
                # Wallet balance = frei verfügbar + in Orders geblockt + in Positionen gebundene Margin
                wallet_balance = available + frozen + used_margin
                # Bitunix liefert kein marginBalance/equity-Feld → Equity selbst berechnen:
                # Margin Balance (Equity) = Wallet Balance + unrealisierter PnL
                mb = data.get("marginBalance") or data.get("equity")
                margin_balance = _num(mb) if mb is not None else wallet_balance + upnl
                result["available"] = round(available, 2)
                result["margin_balance"] = round(margin_balance, 2)
                result["wallet_balance"] = round(wallet_balance, 2)
                result["unrealized_pnl"] = round(upnl, 2)
            result["bitunix_code"] = bal.get("code") if isinstance(bal, dict) else None
        except Exception as e:
            result["bitunix_error"] = str(e)[:120]

    # ---- Paper overlay: paper trade stats alongside live ----
    # Only add paper stats if mode is live AND there are paper trades in DB
    if mode == "live":
        try:
            paper_open = await state.db.auto_trades.count_documents(
                {"status": "open", "mode": "paper"}
            )
            paper_closed = await state.db.auto_trades.find(
                {"status": "closed", "mode": "paper"}
            ).to_list(500)
            paper_pnl = round(sum(t.get("realized_pnl", 0) for t in paper_closed), 4)
            # Only include if there's actual paper activity
            if paper_open > 0 or paper_pnl != 0 or len(paper_closed) > 0:
                result["paper_pnl"] = paper_pnl
                result["paper_open_trades"] = paper_open
                result["paper_closed_trades"] = len(paper_closed)
        except Exception:
            pass  # Don't break the main balance if paper query fails

    # ---- Kapital-Zuweisung (für Balance-Widget) ----
    try:
        live_total = result.get("wallet_balance")
        alloc_out = {}
        for scope in ("live", "paper"):
            a = autotrader.capital_allocation(scope)
            allocated = await autotrader.allocated_capital(
                scope, total=live_total if scope == "live" else None)
            used = await autotrader.used_margin(scope)
            alloc_out[scope] = {
                "mode": a.get("mode", "full"),
                "value": a.get("value", 0),
                "base_balance": a.get("base_balance"),
                "allocated": round(allocated, 2) if allocated is not None else None,
                "used_margin": round(used, 2),
                "free": round(allocated - used, 2) if allocated is not None else None,
            }
        result["allocation"] = alloc_out
    except Exception as e:
        logger.warning(f"balance allocation info failed: {e}")

    return result
