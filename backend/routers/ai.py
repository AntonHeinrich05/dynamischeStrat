"""KI Trader (AI Trading Engine) Endpoints."""
import json
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from core.auth import require_admin
from services.ai_engine import ai_engine
from services.news_feed import news_feed

router = APIRouter(tags=["ai"])


@router.get("/api/ai/status")
async def ai_status():
    return ai_engine.status()


@router.post("/api/ai/config")
async def ai_config(updates: Dict, _: bool = Depends(require_admin)):
    cfg = await ai_engine.update_config(updates)
    return {"status": "success", "config": cfg}


@router.post("/api/ai/analyze")
async def ai_analyze_now(_: bool = Depends(require_admin)):
    result = await ai_engine.run_analysis(manual=True)
    return result


@router.get("/api/ai/insights")
async def ai_insights():
    """Performance-Statistik + gelernte Lektionen des KI Traders."""
    stats, lessons = {}, []
    if ai_engine.learning:
        try:
            stats = await ai_engine.learning.gather_stats()
        except Exception as e:
            stats = {"error": str(e)[:120]}
        lessons = await ai_engine.learning.get_lessons()
    doc = await ai_engine.db.settings.find_one({"_id": "ai_lessons"}) or {}
    return {"stats": stats, "lessons": lessons,
            "assessment": doc.get("assessment"),
            "last_learn": doc.get("updated_at"), "trigger": doc.get("trigger"),
            "learning": ai_engine.learning.summary() if ai_engine.learning else None}


@router.post("/api/ai/learn")
async def ai_learn_now(_: bool = Depends(require_admin)):
    """Manueller Lernlauf: KI wertet ihre Signal-/Trade-Historie aus."""
    if not ai_engine.learning:
        raise HTTPException(status_code=503, detail="Lern-Modul nicht initialisiert")
    await ai_engine.learning.sync_outcomes()
    return await ai_engine.learning.run_learning(trigger="manual")


@router.get("/api/ai/proposals")
async def ai_proposals(status: str = None, limit: int = 40):
    """Einstellungs-Vorschläge der KI (pending + Historie)."""
    return {"proposals": await ai_engine.list_proposals(status=status, limit=limit)}


@router.post("/api/ai/proposals/{pid}")
async def ai_proposal_decide(pid: str, body: Dict, _: bool = Depends(require_admin)):
    action = (body.get("action") or "").lower()
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action muss approve|reject sein")
    prop = await ai_engine.decide_proposal(pid, action == "approve")
    if not prop:
        raise HTTPException(status_code=404, detail="Vorschlag nicht gefunden oder bereits entschieden")
    return {"status": "success", "proposal": prop}


@router.get("/api/ai/chat/history")
async def ai_chat_history(limit: int = 80):
    return {"messages": await ai_engine.chat_history(limit)}


@router.post("/api/ai/chat")
async def ai_chat(body: Dict, _: bool = Depends(require_admin)):
    text = (body.get("message") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nachricht fehlt")

    # Coin-Filter für den Chat-Kontext (Feature: Coin-Auswahl im KI-Chat).
    # Erlaubt eine Liste von Symbolen; leer / "ALL" => alle Coins.
    coins = body.get("coins")
    if isinstance(coins, str):
        coins = [coins]
    elif not isinstance(coins, list):
        coins = None

    async def gen():
        try:
            async for token in ai_engine.chat_stream(text, coins=coins):
                yield f"data: {json.dumps({'t': token})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.delete("/api/ai/chat")
async def ai_chat_clear(_: bool = Depends(require_admin)):
    await ai_engine.clear_chat()
    return {"status": "success"}


@router.post("/api/ai/summary")
async def ai_summary_now(_: bool = Depends(require_admin)):
    """Erzwingt manuell einen Tages-Reset inkl. Archivierung und generiert eine
    neue markierte Tages-Zusammenfassung (role='summary', pinned)."""
    result = await ai_engine.force_daily_summary()
    return {"status": "success", **result}


@router.get("/api/ai/news")
async def ai_news(limit: int = 20):
    return {"headlines": await news_feed.get_headlines(limit)}
