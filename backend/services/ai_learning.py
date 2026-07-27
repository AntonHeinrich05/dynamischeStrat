"""Selbst-Lernen des KI Traders.

Verknüpft die KI-Entscheidungen (ai_decisions) mit ihren echten Ergebnissen
(Signal-Win/Loss + geschlossene Paper-/Live-Trades), aggregiert daraus eine
Performance-Statistik und lässt das LLM daraus kompakte, umsetzbare
"Lektionen" ableiten. Die Lektionen + Statistik fließen in jede Analyse und
in den Chat ein – unabhängig davon, was der Nutzer schreibt.

Trigger: automatisch nach geschlossenen Trades (mit Mindestabstand),
täglich beim 00:00-Berlin-Reset und manuell per Endpoint.
"""
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

LEARNING_SYSTEM = (
    "Du bist der 'KI Trader' einer Krypto-Daytrading-Plattform und wertest deine EIGENE "
    "Trading-Performance aus, um besser zu werden. Sei brutal ehrlich und rein datenbasiert. "
    "Erkenne Muster: Welche Coins/Richtungen/Konfidenz-Level funktionieren, welche nicht? "
    "Passen SL/TP/Hebel zum beobachteten Verhalten (z.B. SL zu eng -> viele knappe Stop-Outs, "
    "TP zu weit -> Gewinne drehen ins Minus)? "
    "WICHTIG zum Lektions-Gedaechtnis: Deine bisherigen Lektionen bleiben dauerhaft im "
    "Kerngedaechtnis gespeichert und gelten fuer ALLE KI-Modelle. Gib im Feld 'lessons' NUR "
    "NEUE oder AKTUALISIERTE Lektionen zurueck (gleicher Titel = Aktualisierung des Details). "
    "Bestehende Lektionen musst du NICHT erneut auflisten - sie bleiben automatisch erhalten. "
    "Nur wenn eine alte Lektion durch die Daten klar WIDERLEGT ist, trage ihren exakten Titel "
    "in 'remove_lessons' ein, damit sie geloescht wird. "
    "Behalte bewaehrte alte Lektionen bei, verwirf widerlegte, formuliere neue nur bei "
    "ausreichender Datenbasis. Bei sehr wenigen Daten "
    "(<5 abgeschlossene Ergebnisse) sei zurueckhaltend und markiere Lektionen als vorlaeufig. "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown, exakt in diesem Schema:\n"
    '{"assessment": "3-6 Saetze ehrliche Selbsteinschaetzung auf Deutsch", '
    '"lessons": [{"title": "Kurztitel", "detail": "konkrete, umsetzbare Regel auf Deutsch"}], '
    '"remove_lessons": ["exakter Titel einer widerlegten Lektion"], '
    '"config_changes": [{"symbol": "BTCUSDT", "changes": {}, "reason": "kurz"}]}\n'
    "config_changes nur angeben, wenn im Prompt ausdruecklich erlaubt - sonst leere Liste."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def aggregate_performance(signals: List[Dict], trades: List[Dict]) -> Dict:
    """Reine Aggregation (testbar): Signal- und Trade-Listen -> Statistik-Dict."""
    sigs = [s for s in signals if s.get("signal_class") != "PRE_SIGNAL"]
    wins = sum(1 for s in sigs if s.get("result") == "win")
    losses = sum(1 for s in sigs if s.get("result") == "loss")
    decided = wins + losses

    by_symbol: Dict[str, Dict] = {}
    by_action = {"LONG": {"total": 0, "wins": 0, "losses": 0},
                 "SHORT": {"total": 0, "wins": 0, "losses": 0}}
    conf_buckets = {"<70": {"total": 0, "wins": 0, "losses": 0},
                    "70-79": {"total": 0, "wins": 0, "losses": 0},
                    ">=80": {"total": 0, "wins": 0, "losses": 0}}
    for s in sigs:
        d = by_symbol.setdefault(s.get("symbol"), {"signals": 0, "wins": 0, "losses": 0,
                                                   "trades": 0, "pnl": 0.0})
        d["signals"] += 1
        res = s.get("result")
        if res == "win":
            d["wins"] += 1
        elif res == "loss":
            d["losses"] += 1
        a = by_action.get(s.get("type"))
        if a is not None:
            a["total"] += 1
            if res == "win":
                a["wins"] += 1
            elif res == "loss":
                a["losses"] += 1
        conf = s.get("ai_confidence")
        if conf is not None:
            b = conf_buckets["<70" if conf < 70 else ("70-79" if conf < 80 else ">=80")]
            b["total"] += 1
            if res == "win":
                b["wins"] += 1
            elif res == "loss":
                b["losses"] += 1

    closed = [t for t in trades if t.get("status") == "closed"]
    modes: Dict[str, Dict] = {}
    for m in ("paper", "live"):
        mt = [t for t in closed if t.get("mode") == m]
        pnl = sum(float(t.get("realized_pnl", 0) or 0) for t in mt)
        w = sum(1 for t in mt if float(t.get("realized_pnl", 0) or 0) > 0)
        modes[m] = {"count": len(mt), "pnl": round(pnl, 4), "wins": w, "losses": len(mt) - w,
                    "win_rate": round(w / len(mt) * 100, 1) if mt else 0.0,
                    "avg_pnl": round(pnl / len(mt), 4) if mt else 0.0}
        for t in mt:
            d = by_symbol.setdefault(t.get("symbol"), {"signals": 0, "wins": 0, "losses": 0,
                                                       "trades": 0, "pnl": 0.0})
            d["trades"] += 1
            d["pnl"] = round(d["pnl"] + float(t.get("realized_pnl", 0) or 0), 4)

    traded = {s: v for s, v in by_symbol.items() if v["trades"] > 0}
    best = max(traded, key=lambda s: traded[s]["pnl"]) if traded else None
    worst = min(traded, key=lambda s: traded[s]["pnl"]) if traded else None

    return {
        "totals": {
            "signals": len(sigs), "signal_wins": wins, "signal_losses": losses,
            "signal_win_rate": round(wins / decided * 100, 1) if decided else 0.0,
            "closed_trades": len(closed),
            "open_trades": sum(1 for t in trades if t.get("status") == "open"),
            "total_pnl": round(modes["paper"]["pnl"] + modes["live"]["pnl"], 4),
        },
        "by_symbol": by_symbol,
        "by_action": by_action,
        "confidence_buckets": conf_buckets,
        "trades": modes,
        "best_symbol": best,
        "worst_symbol": worst,
    }


def performance_to_text(stats: Dict) -> str:
    t = stats.get("totals", {})
    tr = stats.get("trades", {})
    lines = [
        f"Signale (letzte {stats.get('lookback_days', '?')} Tage): {t.get('signals', 0)} gesamt, "
        f"{t.get('signal_wins', 0)} Win / {t.get('signal_losses', 0)} Loss "
        f"(Winrate {t.get('signal_win_rate', 0)}%)",
    ]
    for m, label in (("paper", "Paper"), ("live", "LIVE")):
        d = tr.get(m, {})
        if d.get("count"):
            lines.append(f"{label}-Trades: {d['count']} geschlossen, PnL {d['pnl']:+.2f} USDT, "
                         f"Winrate {d['win_rate']}%, O {d['avg_pnl']:+.2f} USDT/Trade")
    if not tr.get("paper", {}).get("count") and not tr.get("live", {}).get("count"):
        lines.append("Trades: noch keine geschlossenen KI-Trades")
    ba = stats.get("by_action", {})
    for a in ("LONG", "SHORT"):
        d = ba.get(a, {})
        dec = d.get("wins", 0) + d.get("losses", 0)
        if d.get("total"):
            wr = round(d["wins"] / dec * 100, 1) if dec else 0.0
            lines.append(f"{a}: {d['total']} Signale, Winrate {wr}%")
    cb = stats.get("confidence_buckets", {})
    cb_parts = []
    for k, d in cb.items():
        dec = d.get("wins", 0) + d.get("losses", 0)
        if dec:
            cb_parts.append(f"{k}%: {round(d['wins'] / dec * 100)}% Winrate ({dec} entschieden)")
    if cb_parts:
        lines.append("Nach Konfidenz: " + " | ".join(cb_parts))
    sym = stats.get("by_symbol", {})
    sym_parts = []
    for s, d in sorted(sym.items(), key=lambda kv: kv[1]["pnl"], reverse=True):
        if d["signals"] or d["trades"]:
            dec = d["wins"] + d["losses"]
            wr = f", Winrate {round(d['wins'] / dec * 100)}%" if dec else ""
            pnl = f", PnL {d['pnl']:+.2f}" if d["trades"] else ""
            sym_parts.append(f"{s}: {d['signals']} Sig{wr}{pnl}")
    if sym_parts:
        lines.append("Pro Coin: " + " | ".join(sym_parts[:13]))
    if stats.get("best_symbol"):
        lines.append(f"Bester Coin (PnL): {stats['best_symbol']} | Schwaechster: {stats.get('worst_symbol')}")
    return "\n".join(lines)


class AILearning:
    def __init__(self, engine):
        self.engine = engine
        self.last_learn: Optional[str] = None
        self.learning_now = False
        self._lessons_cache: Optional[List[Dict]] = None
        self._last_tick = 0.0
        self._last_learn_ts = 0.0
        self.min_learn_gap_sec = 900  # max. 1 Trade-Close-Lernlauf pro 15 min

    @property
    def db(self):
        return self.engine.db

    async def load_state(self):
        try:
            doc = await self.db.settings.find_one({"_id": "ai_lessons"})
            if doc:
                self._lessons_cache = doc.get("lessons", [])
                self.last_learn = doc.get("updated_at")
        except Exception as e:
            logger.warning(f"AI lessons load failed: {e}")

    # ---------------- outcome sync ----------------
    async def sync_outcomes(self) -> List[Dict]:
        """Schreibt Signal-Ergebnisse & Trade-PnL zurueck in ai_decisions.
        Gibt die NEU geschlossenen KI-Trades zurueck (Lern-Trigger)."""
        try:
            sigs = await self.db.signals.find({
                "strategy_id": "ai_trader",
                "result": {"$in": ["win", "loss", "breakeven"]},
                "ai_learn_synced": {"$ne": True},
            }).limit(200).to_list(200)
            for s in sigs:
                if s.get("id"):
                    await self.db.ai_decisions.update_many(
                        {"signal_id": s["id"]},
                        {"$set": {"outcome": s.get("result"), "outcome_ts": _now_iso()}})
                    await self.db.signals.update_one(
                        {"id": s["id"]}, {"$set": {"ai_learn_synced": True}})
        except Exception as e:
            logger.warning(f"AI outcome sync (signals) failed: {e}")

        new_trades: List[Dict] = []
        try:
            trades = await self.db.auto_trades.find({
                "strategy_id": "ai_trader", "status": "closed",
                "ai_learn_synced": {"$ne": True},
            }).limit(100).to_list(100)
            for t in trades:
                await self.db.auto_trades.update_one(
                    {"id": t["id"]}, {"$set": {"ai_learn_synced": True}})
                if t.get("signal_id"):
                    await self.db.ai_decisions.update_many(
                        {"signal_id": t["signal_id"]},
                        {"$set": {"trade_pnl": t.get("realized_pnl"),
                                  "trade_mode": t.get("mode"),
                                  "trade_closed_at": t.get("closed_at")}})
                t.pop("_id", None)
                new_trades.append(t)
        except Exception as e:
            logger.warning(f"AI outcome sync (trades) failed: {e}")
        return new_trades

    # ---------------- stats ----------------
    async def gather_stats(self) -> Dict:
        days = int(self.engine.config.get("learning_lookback_days", 14))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        signals = await self.db.signals.find(
            {"strategy_id": "ai_trader", "timestamp": {"$gte": cutoff}}).to_list(2000)
        trades = await self.db.auto_trades.find(
            {"strategy_id": "ai_trader", "opened_at": {"$gte": cutoff}}).to_list(2000)
        stats = aggregate_performance(signals, trades)
        stats["lookback_days"] = days
        return stats

    async def performance_text(self) -> str:
        try:
            return performance_to_text(await self.gather_stats())
        except Exception as e:
            return f"(Performance-Daten nicht verfuegbar: {str(e)[:80]})"

    # ---------------- lessons ----------------
    async def get_lessons(self) -> List[Dict]:
        if self._lessons_cache is None:
            await self.load_state()
        return self._lessons_cache or []

    def _max_lessons(self) -> int:
        """Maximale Anzahl dauerhaft gespeicherter Lektionen (Kerngedaechtnis)."""
        try:
            val = int(self.engine.config.get("max_lessons", 50) or 50)
        except (TypeError, ValueError):
            val = 50
        return max(5, min(200, val))

    async def lessons_text(self) -> str:
        lessons = await self.get_lessons()
        if not lessons:
            return "(noch keine Lektionen - zu wenige abgeschlossene Ergebnisse)"
        return "\n".join(f"{i + 1}. {l.get('title')}: {l.get('detail')}"
                         for i, l in enumerate(lessons))

    def summary(self) -> Dict:
        return {
            "enabled": bool(self.engine.config.get("learning_enabled", True)),
            "last_learn": self.last_learn,
            "lessons_count": len(self._lessons_cache or []),
            "max_lessons": self._max_lessons(),
            "learning_now": self.learning_now,
        }

    # ---------------- loop hook ----------------
    async def tick(self):
        """Wird vom Engine-Loop aufgerufen (alle ~5s, intern auf 30s gedrosselt)."""
        now = time.time()
        if now - self._last_tick < 30:
            return
        self._last_tick = now
        new_trades = await self.sync_outcomes()
        cfg = self.engine.config
        if (new_trades and cfg.get("learning_enabled", True)
                and cfg.get("learn_on_trade_close", True) and self.engine.key
                and (now - self._last_learn_ts) > self.min_learn_gap_sec):
            await self.run_learning(trigger="trade_close")

    # ---------------- learning run ----------------
    async def _recent_outcomes_text(self, limit: int = 25) -> str:
        trades = await self.db.auto_trades.find(
            {"strategy_id": "ai_trader", "status": "closed"}
        ).sort("closed_at", -1).limit(limit).to_list(limit)
        if not trades:
            return "(noch keine geschlossenen KI-Trades)"
        sig_ids = [t.get("signal_id") for t in trades if t.get("signal_id")]
        dec_by_sig: Dict[str, Dict] = {}
        if sig_ids:
            decs = await self.db.ai_decisions.find(
                {"signal_id": {"$in": sig_ids}}).to_list(len(sig_ids))
            dec_by_sig = {d.get("signal_id"): d for d in decs}
        lines = []
        for t in reversed(trades):
            d = dec_by_sig.get(t.get("signal_id"), {})
            pnl = float(t.get("realized_pnl", 0) or 0)
            dur = ""
            try:
                o = datetime.fromisoformat(str(t.get("opened_at")).replace("Z", "+00:00"))
                c = datetime.fromisoformat(str(t.get("closed_at")).replace("Z", "+00:00"))
                dur = f", Dauer {int((c - o).total_seconds() / 60)}min"
            except Exception:
                pass
            conf = d.get("confidence")
            reason = str(d.get("reasoning", ""))[:110]
            lines.append(
                f"- {t.get('symbol')} {t.get('side')} [{t.get('mode')}] Hebel {t.get('leverage')}x, "
                f"PnL {pnl:+.2f} USDT{dur}"
                + (f", Konfidenz {conf}%" if conf is not None else "")
                + (f" | Begruendung damals: {reason}" if reason else ""))
        return "\n".join(lines)

    async def run_learning(self, trigger: str = "manual") -> Dict:
        if self.learning_now:
            return {"status": "busy", "detail": "Lernlauf laeuft bereits"}
        if not self.engine.key:
            return {"status": "error", "detail": "Kein API-Key fuer den aktiven Provider"}
        self.learning_now = True
        try:
            stats = await self.gather_stats()
            stats_txt = performance_to_text(stats)
            outcomes_txt = await self._recent_outcomes_text()
            old = await self.get_lessons()
            old_txt = "\n".join(f"- {l.get('title')}: {l.get('detail')}" for l in old) or "(keine)"
            directives = await self.engine._user_directives(10)
            max_lessons = self._max_lessons()
            autonomy = self.engine.config.get("autonomy", "suggest")
            autonomy_block = ""
            if autonomy in ("suggest", "auto"):
                from services.ai_knowledge import tunable_spec_text
                autonomy_block = (
                    "\n\nDu DARFST zusaetzlich datenbasierte Aenderungen an deinen Trade-Einstellungen "
                    "zurueckgeben (Feld config_changes, max. 4; symbol \"ENGINE\" fuer "
                    "min_confidence/cooldown_min). NIE max_capital oder mode.\n" + tunable_spec_text())
            prompt = (
                f"=== PERFORMANCE-STATISTIK (letzte {stats.get('lookback_days')} Tage) ===\n{stats_txt}\n\n"
                f"=== LETZTE GESCHLOSSENE TRADES (chronologisch) ===\n{outcomes_txt}\n\n"
                f"=== BISHERIGE LEKTIONEN (bleiben gespeichert) ===\n{old_txt}\n\n"
                f"=== AKTUELLE TRADER-DIREKTIVEN ===\n{directives}\n\n"
                f"Aktuell sind {len(old)} von max. {max_lessons} Lektionen im Kerngedaechtnis. "
                f"Gib im Feld 'lessons' NUR neue oder aktualisierte Lektionen zurueck "
                f"(gleicher Titel = Aktualisierung). Bestehende bleiben automatisch erhalten. "
                f"Trage in 'remove_lessons' nur exakte Titel klar widerlegter Lektionen ein."
                f"{autonomy_block}"
            )
            raw, model_used = await self.engine._generate_json(prompt, LEARNING_SYSTEM)
            data = self.engine._parse_json(raw)

            # --- Neue/aktualisierte Lektionen aus der LLM-Antwort einsammeln ---
            incoming: List[Dict] = []
            for l in (data.get("lessons") or []):
                if isinstance(l, dict) and l.get("title"):
                    incoming.append({"title": str(l["title"])[:120],
                                     "detail": str(l.get("detail", ""))[:400]})

            # --- Titel, die explizit geloescht werden sollen (widerlegt) ---
            remove_titles = set()
            for rt in (data.get("remove_lessons") or []):
                if isinstance(rt, str) and rt.strip():
                    remove_titles.add(rt.strip().lower())

            # --- MERGE: bestehende Lektionen behalten (nicht ueberschreiben!) ---
            merged: List[Dict] = []
            index_by_title: Dict[str, int] = {}
            for l in old:
                if not isinstance(l, dict) or not l.get("title"):
                    continue
                key = str(l["title"]).strip().lower()
                if key in remove_titles:
                    continue  # widerlegte Lektion loeschen
                index_by_title[key] = len(merged)
                merged.append({"title": str(l["title"])[:120],
                               "detail": str(l.get("detail", ""))[:400]})

            # --- Neue anhaengen / gleiche Titel aktualisieren ---
            added, updated, skipped = 0, 0, 0
            skipped_titles: List[str] = []
            for l in incoming:
                key = l["title"].strip().lower()
                if key in remove_titles:
                    continue
                if key in index_by_title:
                    merged[index_by_title[key]] = l  # Aktualisierung (zaehlt nicht gegen Limit)
                    updated += 1
                elif len(merged) < max_lessons:
                    index_by_title[key] = len(merged)
                    merged.append(l)  # neue Lektion anhaengen
                    added += 1
                else:
                    # Option b: Maximum erreicht -> neue Lektion verwerfen + Warnung
                    skipped += 1
                    skipped_titles.append(l["title"])

            if skipped:
                logger.warning(
                    f"AI learning: Lektions-Limit ({max_lessons}) erreicht, "
                    f"{skipped} neue Lektion(en) verworfen: {skipped_titles}")

            lessons = merged
            removed_count = sum(1 for l in old
                                if isinstance(l, dict) and l.get("title")
                                and str(l["title"]).strip().lower() in remove_titles)
            assessment = str(data.get("assessment", ""))[:1200]
            now = _now_iso()
            await self.db.settings.update_one(
                {"_id": "ai_lessons"},
                {"$set": {"lessons": lessons, "assessment": assessment, "updated_at": now,
                          "trigger": trigger, "model": model_used,
                          "max_lessons": max_lessons,
                          "stats": stats.get("totals", {})}},
                upsert=True)
            self._lessons_cache = lessons
            self.last_learn = now
            self._last_learn_ts = time.time()

            cfg_results = []
            try:
                cfg_results = await self.engine._handle_config_changes(
                    data.get("config_changes") or [], source="learning")
            except Exception as ce:
                logger.error(f"Learning config changes failed: {ce}")

            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "learning",
                "text": assessment, "lessons": lessons,
                "trigger": trigger, "model": model_used, "ts": now,
            })
            logger.info(f"AI learning done ({trigger}, {model_used}): "
                        f"{len(lessons)}/{max_lessons} Lektionen gesamt "
                        f"(+{added} neu, ~{updated} aktualisiert, -{removed_count} entfernt, "
                        f"{skipped} verworfen), {len(cfg_results)} Config-Aenderungen")
            result = {"status": "ok", "lessons": len(lessons),
                      "lessons_added": added, "lessons_updated": updated,
                      "lessons_removed": removed_count, "lessons_skipped": skipped,
                      "max_lessons": max_lessons,
                      "assessment": assessment,
                      "config_changes": len(cfg_results), "model": model_used}
            if skipped:
                result["warning"] = (
                    f"Maximum von {max_lessons} Lektionen erreicht - "
                    f"{skipped} neue Lektion(en) wurden verworfen. "
                    f"Erhoehe max_lessons oder lass die KI widerlegte Lektionen entfernen.")
            return result
        except Exception as e:
            logger.error(f"AI learning failed: {e}")
            return {"status": "error", "detail": str(e)[:300]}
        finally:
            self.learning_now = False