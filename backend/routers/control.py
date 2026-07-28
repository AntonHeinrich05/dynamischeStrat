"""Admin control toggles (Stop All Trades / Stop All Signals)."""
import logging

from fastapi import APIRouter, Depends

from core import state
from core.auth import require_admin
from core.state import scanner, autotrader, control_state
from core.pipeline import broadcast

logger = logging.getLogger(__name__)

router = APIRouter(tags=["control"])


async def _persist_control_state():
    await state.db.settings.update_one(
        {"_id": "control_state"},
        {"$set": {"trades_paused": control_state["trades_paused"],
                  "signals_paused": control_state["signals_paused"]}},
        upsert=True,
    )


async def _close_all_open_auto_trades() -> int:
    """Close every bot-opened auto-trade currently 'open' in our DB.
    Manual/user-placed positions on the Bitunix account are NOT touched
    because they are not tracked in the auto_trades collection."""
    closed = 0
    async for t in state.db.auto_trades.find({"status": "open"}):
        price = scanner.current_price(t["symbol"]) or t.get("entry")
        try:
            res = await autotrader.manual_close(t["id"], price)
            if res:
                closed += 1
        except Exception as e:
            logger.error(f"auto-close {t.get('id')} failed: {e}")
    return closed


@router.get("/api/control/state")
async def get_control_state():
    return {"trades_paused": control_state["trades_paused"],
            "signals_paused": control_state["signals_paused"]}


@router.post("/api/control/stop-trades")
async def toggle_stop_trades(_: bool = Depends(require_admin)):
    """Toggle 'Stop All Trades'. When switched ON, closes every bot-opened
    auto-trade in our DB and blocks the bot from opening new ones.
    Manual trades placed by the user directly on Bitunix are not affected.
    When switched OFF, the bot resumes with the previous per-coin config."""
    new_val = not control_state["trades_paused"]
    control_state["trades_paused"] = new_val
    closed = 0
    if new_val:
        closed = await _close_all_open_auto_trades()
    await _persist_control_state()
    await broadcast({"type": "control_state", "data": dict(control_state)})
    return {"status": "success", "trades_paused": new_val, "closed_trades": closed}


@router.post("/api/control/stop-signals")
async def toggle_stop_signals(_: bool = Depends(require_admin)):
    """Toggle 'Stop All Signals'. When ON, signals are not emitted, saved or
    broadcast. When OFF, signal emission resumes exactly with the previously
    enabled strategies (no config touched)."""
    new_val = not control_state["signals_paused"]
    control_state["signals_paused"] = new_val
    await _persist_control_state()
    await broadcast({"type": "control_state", "data": dict(control_state)})
    return {"status": "success", "signals_paused": new_val}
