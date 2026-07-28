#!/usr/bin/env python3
"""Lokaler Worker für Backtests, Strategie-Optimierung & Strategie-Discovery.

Nutzt EXAKT denselben Code wie der Server (services/ + strategies/ liegen
daneben bzw. im Repo unter backend/). Verbindet sich per Outbound-Polling
mit der Website – keine Portfreigaben oder Router-Einstellungen nötig.

Start:
    python worker.py --server https://deine-website.example --token DEIN_TOKEN
Optionen werden in worker_config.json gespeichert und müssen nur einmal
angegeben werden. Daten-Ordner ändern: --data-dir "D:/KryptoDaten"
"""
import argparse
import asyncio
import gzip
import json
import logging
import os
import platform
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

WORKER_VERSION = "1.5.0"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "worker_config.json"

# Rechen-Jobs laufen in einem eigenen Thread. Ein kurzes Umschaltintervall
# stellt sicher, dass der Poll-/Heartbeat-Task auch während langer
# numpy-/Python-Blöcke regelmäßig drankommt (sonst meldet der Server "offline").
sys.setswitchinterval(0.002)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("worker")


# ---------------- Konfiguration ----------------
def load_config(args) -> dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (ValueError, OSError):
            cfg = {}
    if args.server:
        cfg["server"] = args.server.rstrip("/")
    if args.token:
        cfg["token"] = args.token
    if args.data_dir:
        cfg["data_dir"] = args.data_dir
    if args.name:
        cfg["name"] = args.name
    cfg.setdefault("name", platform.node() or "Lokaler PC")
    cfg.setdefault("data_dir", str(Path.home() / "KryptoScannerDaten"))
    cfg.setdefault("worker_id", uuid.uuid4().hex[:16])
    cfg.setdefault("ram_limit_mb", 4096)
    cfg.setdefault("max_parallel_jobs", 1)
    if not cfg.get("server") or not cfg.get("token"):
        print("\nFEHLER: Server-URL und Token erforderlich.\n"
              "  python worker.py --server https://deine-website --token DEIN_TOKEN\n"
              "Token findest du in der Website: Backtester/Optimizer -> Lokale Ausführung -> Verwalten.\n")
        sys.exit(1)
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except OSError:
        pass
    return cfg


def setup_modules(cfg):
    """services/strategies auffindbar machen + Kerzen-Cache auf Daten-Ordner zeigen."""
    if (BASE_DIR / "services").is_dir():
        code_dir = BASE_DIR
    elif (BASE_DIR.parent / "backend" / "services").is_dir():
        code_dir = BASE_DIR.parent / "backend"
    else:
        print("FEHLER: services/-Ordner nicht gefunden. Worker-Zip komplett entpacken "
              "oder Worker im Repo unter local_worker/ starten.")
        sys.exit(1)
    sys.path.insert(0, str(code_dir))
    data_dir = Path(cfg["data_dir"]).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["CANDLE_CACHE_DIR"] = str(data_dir)
    # Kerzen liegen spaltenbasiert im RAM (~48 Byte/Kerze) -> 1 MB ≈ 20.000 Kerzen
    os.environ["CANDLE_CACHE_MAX_CANDLES"] = str(int(cfg.get("ram_limit_mb", 4096)) * 20000)
    os.environ["CANDLE_CACHE_DISK"] = "1"
    # Multi-Core: 0 = alle Kerne (wird per Website-Einstellung überschrieben)
    os.environ.setdefault("SIM_WORKERS", "0")


# nach setup_modules() importiert (siehe main)
bt = opt = cc = registry_mod = rlab = ropt = None


def _import_services():
    global bt, opt, cc, registry_mod, rlab, ropt
    from services import backtester as _bt
    from services import optimizer as _opt
    from services import candle_cache as _cc
    from services import regime_lab as _rlab
    from services import regime_opt as _ropt
    from strategies import registry as _reg
    bt, opt, cc, registry_mod, rlab, ropt = _bt, _opt, _cc, _reg, _rlab, _ropt


# ---------------- Daten-Index (Inventar für die Website) ----------------
class DataIndex:
    def __init__(self):
        self.path = Path(cc.CACHE_DIR) / "index.json"
        self.meta = {}
        self._summary_cache = None
        self._summary_at = 0.0
        try:
            self.meta = json.loads(self.path.read_text())
        except (ValueError, OSError):
            self.meta = {}

    def _save(self):
        self._summary_cache = None
        try:
            self.path.write_text(json.dumps(self.meta, indent=1))
        except OSError:
            pass

    def _file_bytes(self, symbol):
        for row in cc.list_disk_symbols():
            if row["symbol"] == symbol:
                return row["bytes"]
        return 0

    def update_from_cache(self, symbol):
        m = cc.cached_meta(symbol) or cc.disk_meta(symbol)
        if not m:
            return
        self.meta[symbol] = {**m, "bytes": self._file_bytes(symbol),
                             "updated": datetime.now(timezone.utc).isoformat()}
        self._save()

    def remove(self, symbol):
        self.meta.pop(symbol, None)
        self._save()

    async def build_missing(self):
        """Vorhandene Daten-Dateien ohne Index-Eintrag indizieren.
        Liest nur die Kopfzeilen per memmap – lädt keine Millionen Kerzen in den RAM."""
        changed = False
        for row in cc.list_disk_symbols():
            sym = row["symbol"]
            if sym in self.meta and self.meta[sym].get("bytes") == row["bytes"]:
                continue
            try:
                m = await asyncio.to_thread(cc.disk_meta, sym)
                if m is None:  # altes .pkl.gz -> einmalig laden & migrieren
                    ca = await asyncio.to_thread(cc._load_disk, sym)
                    m = ({"candles": len(ca), "first_ts": int(ca.ts[0]),
                          "last_ts": int(ca.ts[-1])} if ca is not None and len(ca) else None)
                if m:
                    self.meta[sym] = {**m, "bytes": self._file_bytes(sym),
                                      "updated": datetime.now(timezone.utc).isoformat()}
                    changed = True
            except Exception as e:
                logger.warning(f"Index {sym}: {e}")
        if changed:
            self._save()

    def summary(self, max_age=10.0):
        """Wird bei jedem Poll (alle 2s) gebraucht -> kurz gecacht, damit die
        Verzeichnis-/Disk-Abfragen den Heartbeat nicht ausbremsen."""
        now = time.time()
        if self._summary_cache is not None and now - self._summary_at < max_age:
            return self._summary_cache
        try:
            disk_free = round(shutil.disk_usage(cc.CACHE_DIR).free / 1e9, 1)
        except OSError:
            disk_free = None
        symbols = []
        for r in cc.list_disk_symbols():
            m = self.meta.get(r["symbol"], {})
            symbols.append({"symbol": r["symbol"], "bytes": r["bytes"],
                            "candles": m.get("candles"), "first_ts": m.get("first_ts"),
                            "last_ts": m.get("last_ts"), "updated": m.get("updated")})
        self._summary_cache = {"dir": cc.CACHE_DIR,
                               "total_bytes": sum(s["bytes"] for s in symbols),
                               "disk_free_gb": disk_free, "symbols": symbols}
        self._summary_at = now
        return self._summary_cache


# ---------------- Ressourcen ----------------
def resources(cpu_cores_setting=0):
    cores = os.cpu_count() or 1
    out = {"cores": cores, "cores_used": cpu_cores_setting or cores,
           "platform": platform.system()}
    try:
        import psutil
        vm = psutil.virtual_memory()
        out.update({"cpu_percent": psutil.cpu_percent(interval=None),
                    "ram_used_mb": round((vm.total - vm.available) / 1e6),
                    "ram_total_mb": round(vm.total / 1e6)})
    except ImportError:
        pass
    return out


def gpu_info():
    try:
        from services import gpu_accel
        gi = gpu_accel.info()
        if gi.get("available"):
            return gi
    except Exception:  # noqa: BLE001
        pass
    try:
        import torch
        if torch.cuda.is_available():
            return {"available": True, "enabled": False, "backend": "torch",
                    "name": torch.cuda.get_device_name(0),
                    "note": "Für GPU-Beschleunigung CuPy installieren: pip install cupy-cuda12x"}
    except ImportError:
        pass
    return {"available": False,
            "note": "Keine NVIDIA-GPU/CuPy gefunden – CPU-Modus (pip install cupy-cuda12x)"}


def _sim_workers_effective():
    from services import parallel_sim
    return parallel_sim.workers_configured()


# ---------------- HTTP-Helfer ----------------
class Api:
    """Steuerkanal zum Server.

    Wichtig: Antworten werden IMMER defensiv ausgewertet. Proxys/Ingress liefern
    unter Last gelegentlich beschädigte Antworten (z.B. Chunk-Reste vor dem JSON).
    Vorher ist der Worker daran mit `json.decoder.JSONDecodeError` gestorben –
    dann kam kein Heartbeat mehr an und laufende Jobs hingen in der Website fest.
    """

    def __init__(self, session, server, token=None):
        self.session = session
        self.server = server
        self.token = token
        self.bad_responses = 0

    @staticmethod
    def make_session(token):
        import aiohttp
        # force_close: kein Keep-Alive. Der Steuerkanal sendet nur kleine
        # Nachrichten (alle 2 s) – dafür ist eine frische Verbindung billig und
        # eine desynchronisierte Verbindung damit ausgeschlossen.
        connector = aiohttp.TCPConnector(limit=8, force_close=True,
                                        ttl_dns_cache=600, enable_cleanup_closed=True)
        return aiohttp.ClientSession(headers={"X-Worker-Token": token},
                                     connector=connector)

    async def reset(self):
        """Session neu aufbauen (nach beschädigten Antworten / Netzproblemen)."""
        try:
            await self.session.close()
        except Exception:  # noqa: BLE001
            pass
        self.session = self.make_session(self.token)
        self.bad_responses = 0
        logger.info("Server-Verbindung neu aufgebaut")

    def _parse(self, raw: bytes, path: str) -> Dict:
        if not raw:
            return {}
        txt = raw.decode("utf-8", "replace")
        try:
            data = json.loads(txt)
            return data if isinstance(data, dict) else {}
        except ValueError:
            pass
        # Beschädigte Antwort: erstes vollständiges JSON-Objekt herausschneiden
        i = txt.find("{")
        if i >= 0:
            try:
                obj, _ = json.JSONDecoder().raw_decode(txt[i:])
                if isinstance(obj, dict):
                    self.bad_responses += 1
                    logger.warning(f"Beschädigte Antwort von {path} – "
                                   f"Nutzdaten gerettet, Rest verworfen")
                    return obj
            except ValueError:
                pass
        self.bad_responses += 1
        logger.warning(f"Ungültige Antwort von {path}: {txt[:120]!r}")
        return {}

    async def post(self, path, payload, compress=False):
        import aiohttp
        url = f"{self.server}{path}"
        if compress:
            data = gzip.compress(json.dumps(payload).encode())
            headers = {"Content-Type": "application/json", "Content-Encoding": "gzip"}
            async with self.session.post(url, data=data, headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=300)) as r:
                raw, status = await r.read(), r.status
        else:
            async with self.session.post(url, json=payload,
                                         timeout=aiohttp.ClientTimeout(total=60)) as r:
                raw, status = await r.read(), r.status
        if status < 400:
            self.bad_responses = max(self.bad_responses - 1, 0)
        return status, self._parse(raw, path)


# ---------------- Job-Ausführung (identischer Code wie der Server) ----------------
def _mk_job(jobs_dict, job_id):
    jobs_dict[job_id] = {"id": job_id, "status": "running", "progress": 0,
                         "phase": "Startet (lokal)", "params": {}, "cancel": False,
                         "created_at": datetime.now(timezone.utc).isoformat(),
                         "result": None, "error": None}
    return jobs_dict[job_id]


async def _relay_progress(api, job_id, job, extra_best=False):
    """Fortschritt an den Server melden bis der Job fertig ist; Abbruch übernehmen.
    Zusätzlich alle ~6 s eine Zeile im Worker-Fenster, damit sichtbar ist, was
    der PC gerade rechnet (Phase, Fortschritt, RAM, Kerne)."""
    t0 = time.time()
    last_log = 0.0
    last_phase = None
    while job["status"] == "running":
        payload = {"progress": job.get("progress"), "phase": job.get("phase")}
        if extra_best and job.get("best") is not None:
            payload["best"] = job["best"]
        try:
            _, resp = await api.post(f"/api/worker/job/{job_id}/progress", payload)
            if resp.get("cancel"):
                job["cancel"] = True
        except Exception as e:
            logger.warning(f"Progress-Meldung fehlgeschlagen: {e}")
        now = time.time()
        phase = job.get("phase")
        if now - last_log >= 6 or phase != last_phase:
            r = resources()
            logger.info(f"  … {job.get('progress') or 0:>3}% · {phase or '–'} "
                        f"· {int(now - t0)}s · RAM {r.get('ram_used_mb', 0)} MB "
                        f"· {_sim_workers_effective()} Prozesse")
            last_log, last_phase = now, phase
        await asyncio.sleep(1.2)


def _run_isolated(coro_factory):
    """Startet eine Coroutine in einem eigenen Thread mit eigener Event-Loop.
    Wichtig: Die Rechenjobs (run_optimizer/run_backtest) enthalten synchronen
    Numpy-/Pandas-Code (FastSeries-Init über hunderttausende Kerzen,
    aggregate_candles, gc.collect()), der die Loop mehrere Sekunden blockiert.
    Läuft er im Haupt-Loop, fällt in der Zeit der Poll-Heartbeat aus und der
    Server markiert den Worker als offline. In einem separaten Thread kann der
    Poll-Loop völlig ungestört weiter Pings senden."""
    def _target():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro_factory())
        finally:
            try:
                loop.close()
            except Exception:
                pass
            asyncio.set_event_loop(None)
    return asyncio.to_thread(_target)


async def _post_result(api, job_id, payload, compress=False):
    """Ergebnis melden – mit Wiederholversuchen, damit ein kurzer Netz-Hänger
    den Job nicht als 'Worker antwortet nicht' verhungern lässt."""
    for attempt in range(5):
        try:
            code, _ = await api.post(f"/api/worker/job/{job_id}/result", payload,
                                     compress=compress)
            if code and code < 500:
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Ergebnis-Upload Versuch {attempt + 1} fehlgeschlagen: {e}")
        await asyncio.sleep(2 + attempt * 3)
    logger.error(f"Ergebnis für Job {job_id} konnte nicht übertragen werden")
    return False


async def handle_backtest(api, job_spec, index):
    job_id = job_spec["job_id"]
    a = job_spec["payload"]["args"]
    registry_mod.registry.load_custom(job_spec["payload"].get("custom_definitions") or [])
    job = _mk_job(bt.JOBS, job_id)
    logger.info(f"Backtest {job_id}: {a['strategy_ids']} auf {a['symbols']} "
                f"({a['days']} Tage, TF {a.get('default_timeframe') or '?'}) "
                f"· {_sim_workers_effective()} Prozesse · lokal")
    t_start = time.time()
    relay = asyncio.create_task(_relay_progress(api, job_id, job))
    try:
        # Rechnung in eigenem Thread/Loop -> Poll-Heartbeat bleibt aktiv.
        await _run_isolated(lambda: bt.run_backtest(
            job_id, a["strategy_ids"], a["symbols"], a["days"], a["cfg"],
            registry_mod.registry, a["settings"], None, a.get("strategy_configs"),
            a.get("default_timeframe"), a.get("date_from"), a.get("date_to")))
    except MemoryError:
        job["status"], job["error"] = "error", (
            "Zu wenig Arbeitsspeicher. Zeitraum verkleinern, weniger Coins wählen "
            "oder das RAM-Limit in den Worker-Einstellungen erhöhen.")
        logger.exception(f"Backtest {job_id}: MemoryError")
    except Exception as e:  # noqa: BLE001 – Job darf nie stillschweigend sterben
        job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"[:400]
        logger.exception(f"Backtest {job_id} abgebrochen")
    finally:
        relay.cancel()
    payload = {"kind": "backtest", "status": job["status"], "error": job["error"],
               "result": job["result"],
               "export_trades": (job.get("export_trades") or [])[:50000]}
    await _post_result(api, job_id, payload, compress=True)
    for sym in a["symbols"]:  # frisch geladene Kerzen dauerhaft speichern
        try:
            if await cc.persist_symbol_async(sym):
                await asyncio.to_thread(index.update_from_cache, sym)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Speichern von {sym} fehlgeschlagen: {e}")
    res = job.get("result") or {}
    pairs = res.get("per_pair") or []
    trades = sum(int(p.get("trades") or 0) for p in pairs)
    candles = sum(int(p.get("candles") or 0) for p in pairs)
    bt.JOBS.pop(job_id, None)
    logger.info(f"Backtest {job_id} fertig: {job['status']} · {int(time.time() - t_start)}s "
                f"· {candles} Kerzen · {trades} Trades"
                + (f" · Fehler: {job['error']}" if job.get("error") else ""))


async def handle_optimizer(api, job_spec, index):
    job_id = job_spec["job_id"]
    a = job_spec["payload"]["args"]
    registry_mod.registry.load_custom(job_spec["payload"].get("custom_definitions") or [])
    job = _mk_job(opt.JOBS, job_id)
    job["best"] = None
    body = a["body"]
    logger.info(f"Optimizer {job_id}: mode={body.get('mode')} auf {body.get('symbols')} "
                f"({body.get('days')} Tage, TF {body.get('timeframe')}, "
                f"{body.get('iterations')} Iterationen) "
                f"· {_sim_workers_effective()} Prozesse · lokal")
    t_start = time.time()
    relay = asyncio.create_task(_relay_progress(api, job_id, job, extra_best=True))
    try:
        await _run_isolated(lambda: opt.run_optimizer(
            job_id, body, registry_mod.registry, a["settings"], a["default_cfg"], None))
    except MemoryError:
        job["status"], job["error"] = "error", (
            "Zu wenig Arbeitsspeicher. Zeitraum verkleinern, weniger Coins wählen "
            "oder das RAM-Limit in den Worker-Einstellungen erhöhen.")
        logger.exception(f"Optimizer {job_id}: MemoryError")
    except Exception as e:  # noqa: BLE001
        job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"[:400]
        logger.exception(f"Optimizer {job_id} abgebrochen")
    finally:
        relay.cancel()
    payload = {"kind": "optimizer", "status": job["status"], "error": job["error"],
               "result": job["result"], "best": job.get("best"),
               "export_trades": (job.get("export_trades") or [])[:25000]}
    await _post_result(api, job_id, payload, compress=True)
    for sym in (body.get("symbols") or []):
        try:
            if await cc.persist_symbol_async(sym):
                await asyncio.to_thread(index.update_from_cache, sym)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Speichern von {sym} fehlgeschlagen: {e}")
    res = job.get("result") or {}
    bench = (res.get("benchmark") or {}) if isinstance(res, dict) else {}
    opt.JOBS.pop(job_id, None)
    logger.info(f"Optimizer {job_id} fertig: {job['status']} · {int(time.time() - t_start)}s"
                + (f" · {bench.get('evaluations')} Bewertungen" if bench.get("evaluations") else "")
                + (f" · Fehler: {job['error']}" if job.get("error") else ""))


async def handle_regime_lab(api, job_spec, index):
    """Regime-Lab-Jobs lokal rechnen: Analyse, Strategie-Suche je Regime,
    finaler Walk-Forward. Persistiert wird serverseitig – der Worker schickt
    das komplette Ergebnis zurück."""
    job_id = job_spec["job_id"]
    a = job_spec["payload"]["args"]
    registry_mod.registry.load_custom(job_spec["payload"].get("custom_definitions") or [])
    fn = a.get("fn")
    body = a.get("body") or {}
    job = _mk_job(rlab.JOBS, job_id)
    logger.info(f"Regime-Lab {job_id}: {fn} "
                f"(Analyse {body.get('analysis_id') or 'neu'}, "
                f"Regime {body.get('regime_id')}) "
                f"· {_sim_workers_effective()} Prozesse · lokal")
    t_start = time.time()
    relay = asyncio.create_task(_relay_progress(api, job_id, job))
    try:
        if fn == "analysis":
            await _run_isolated(lambda: rlab.run_analysis(job_id, body, None))
        elif fn == "regime_opt":
            await _run_isolated(lambda: ropt.run_regime_optimizer(
                job_id, body, registry_mod.registry, a["settings"], a["default_cfg"], None))
        elif fn == "walkforward":
            await _run_isolated(lambda: ropt.run_walkforward(
                job_id, body, registry_mod.registry, a["settings"], a["default_cfg"], None))
        else:
            job["status"], job["error"] = "error", f"Unbekannter Regime-Lab-Job: {fn}"
    except MemoryError:
        job["status"], job["error"] = "error", (
            "Zu wenig Arbeitsspeicher. Zeitraum verkleinern, weniger Coins wählen "
            "oder das RAM-Limit in den Worker-Einstellungen erhöhen.")
        logger.exception(f"Regime-Lab {job_id}: MemoryError")
    except Exception as e:  # noqa: BLE001
        job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"[:400]
        logger.exception(f"Regime-Lab {job_id} abgebrochen")
    finally:
        relay.cancel()
    payload = {"kind": "regime_lab", "status": job["status"], "error": job["error"],
               "result": job.get("result")}
    await _post_result(api, job_id, payload, compress=True)
    syms = (body.get("symbols") or
            ((body.get("analysis_doc") or {}).get("symbols") or []))
    for sym in syms:
        try:
            if await cc.persist_symbol_async(sym):
                await asyncio.to_thread(index.update_from_cache, sym)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Speichern von {sym} fehlgeschlagen: {e}")
    rlab.JOBS.pop(job_id, None)
    logger.info(f"Regime-Lab {job_id} ({fn}) fertig: {job['status']} "
                f"· {int(time.time() - t_start)}s"
                + (f" · Fehler: {job['error']}" if job.get("error") else ""))


async def handle_data_job(api, job_spec, index):
    import aiohttp
    job_id = job_spec["job_id"]
    kind = job_spec["kind"]
    params = job_spec.get("payload") or {}
    local = {"cancel": False, "phase": ""}
    done_syms, errors = [], []

    async def report(progress, phase):
        try:
            _, resp = await api.post(f"/api/worker/job/{job_id}/progress",
                                     {"progress": progress, "phase": phase})
            if resp.get("cancel"):
                local["cancel"] = True
        except Exception:
            pass

    status = "done"
    try:
        if kind == "data_delete":
            sym = params.get("symbol")
            cc.remove_symbol(sym)
            index.remove(sym)
            done_syms = [sym]
        else:
            if kind == "data_update":
                symbols = [r["symbol"] for r in cc.list_disk_symbols()]
                days_map = {}
                now_ms = int(time.time() * 1000)
                for s in symbols:
                    first = (index.meta.get(s) or {}).get("first_ts")
                    days_map[s] = max(1, int((now_ms - first) / 86400000) + 1) if first else 3
            else:  # data_download
                symbols = params.get("symbols") or []
                days_map = {s: int(params.get("days") or 30) for s in symbols}
            if not symbols:
                raise RuntimeError("Keine Daten vorhanden" if kind == "data_update"
                                   else "Keine Coins angegeben")
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            # Eigene Verbindung mit DNS-Cache und begrenzter Anzahl Sockets –
            # bei zehntausenden Requests laufen sonst DNS/NAT-Tabellen über
            # ("getaddrinfo failed").
            connector = aiohttp.TCPConnector(limit=8, limit_per_host=6,
                                             ttl_dns_cache=600,
                                             enable_cleanup_closed=True)
            async with aiohttp.ClientSession(headers=headers,
                                             connector=connector) as data_session:
                for i, sym in enumerate(symbols):
                    if local["cancel"]:
                        status = "cancelled"
                        break
                    total_days = max(int(days_map[sym]), 1)
                    try:
                        # In Etappen laden und nach jeder Etappe speichern:
                        # Ein Abbruch (Netz, Neustart) verliert dadurch nichts,
                        # ein erneuter Start setzt am gespeicherten Stand an.
                        step = 360
                        d = min(step, total_days)
                        while True:
                            if local["cancel"]:
                                status = "cancelled"
                                break
                            await report(round((i + d / total_days) / len(symbols) * 100),
                                         f"Lade {sym}: {d}/{total_days} Tage...")
                            await cc.get_candles(data_session, sym, d, job=local)
                            await cc.persist_symbol_async(sym)
                            await asyncio.to_thread(index.update_from_cache, sym)
                            if d >= total_days:
                                break
                            d = min(d + step, total_days)
                        if status == "cancelled":
                            break
                        done_syms.append(sym)
                    except bt.JobCancelled:
                        status = "cancelled"
                        break
                    except Exception as e:
                        errors.append(f"{sym}: {e}")
                        logger.warning(f"Daten-Job {sym}: {e}")
    except Exception as e:
        status = "error"
        errors.append(str(e))
    if errors and not done_syms and status == "done":
        status = "error"
    payload = {"kind": kind, "status": status,
               "error": "; ".join(errors)[:400] if errors else None,
               "summary": {"symbols": done_syms, "errors": errors,
                           "data": index.summary(max_age=0)}}
    await _post_result(api, job_id, payload)
    logger.info(f"Daten-Job {job_id} ({kind}) fertig: {status} {done_syms}"
                + (f" · Fehler: {'; '.join(errors)[:200]}" if errors else ""))


# ---------------- Auto-Update der lokalen Daten ----------------
async def auto_update_loop(get_settings, busy_check, index):
    import aiohttp
    while True:
        s = get_settings()
        interval = max(int(s.get("auto_update_minutes") or 60), 5)
        await asyncio.sleep(interval * 60)
        if not s.get("auto_update_enabled") or busy_check():
            continue
        try:
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            async with aiohttp.ClientSession(headers=headers) as session:
                for row in cc.list_disk_symbols():
                    sym = row["symbol"]
                    first = (index.meta.get(sym) or {}).get("first_ts")
                    days = max(1, int((time.time() * 1000 - first) / 86400000) + 1) if first else 3
                    await cc.get_candles(session, sym, days)
                    await cc.persist_symbol_async(sym)
                    await asyncio.to_thread(index.update_from_cache, sym)
            logger.info("Auto-Update der lokalen Kerzendaten abgeschlossen")
        except Exception as e:
            logger.warning(f"Auto-Update fehlgeschlagen: {e}")


# ---------------- Hauptschleife ----------------
async def run(cfg):
    import aiohttp
    _import_services()
    index = DataIndex()
    asyncio.create_task(index.build_missing())
    server_settings = {}
    active = {}       # job_id -> Task (Rechen-Jobs)
    active_data = {}  # job_id -> Task (Daten-Jobs)

    def busy():
        return bool(active) or bool(active_data)

    asyncio.create_task(auto_update_loop(lambda: server_settings, busy, index))

    session = Api.make_session(cfg["token"])
    api = Api(session, cfg["server"], cfg["token"])
    logger.info(f"Worker '{cfg['name']}' verbindet zu {cfg['server']} · Daten: {cc.CACHE_DIR}")
    first = True
    fails = 0
    while True:
        try:
            for d in (active, active_data):
                for jid in [j for j, t in d.items() if t.done()]:
                    t = d.pop(jid)
                    exc = t.exception() if not t.cancelled() else None
                    if exc is not None:
                        logger.error(f"Job {jid} mit Fehler beendet: {exc!r}")
            max_par = int(server_settings.get("max_parallel_jobs") or cfg.get("max_parallel_jobs") or 1)
            payload = {
                "worker_id": cfg["worker_id"], "name": cfg["name"],
                "version": WORKER_VERSION,
                "resources": resources(int(server_settings.get("cpu_cores") or 0)),
                "gpu": gpu_info(), "data": index.summary(),
                "sim_workers": _sim_workers_effective(),
                "running_jobs": list(active) + list(active_data),
                "want_compute": len(active) < max_par,
                "want_data": len(active_data) == 0,
            }
            code, resp = await api.post("/api/worker/poll", payload)
            if code == 401:
                logger.error("Token ungültig – neues Token in der Website erzeugen "
                             "und mit --token übergeben.")
                await asyncio.sleep(30)
                continue
            if first:
                logger.info("Verbunden ✓ – warte auf Jobs")
                first = False
            new_settings = resp.get("settings") or {}
            if new_settings != server_settings:
                server_settings = new_settings
                os.environ["SIM_WORKERS"] = str(int(server_settings.get("cpu_cores") or 0))
                os.environ["USE_GPU"] = "1" if server_settings.get("use_gpu") else "0"
                if server_settings.get("ram_limit_mb"):
                    cc.MAX_CANDLES_IN_MEMORY = int(server_settings["ram_limit_mb"]) * 20000
                nd = (server_settings.get("data_dir") or "").strip()
                if nd and nd != cc.CACHE_DIR:
                    Path(nd).expanduser().mkdir(parents=True, exist_ok=True)
                    cc.CACHE_DIR = str(Path(nd).expanduser())
                    cc.clear()
                    index.__init__()
                    asyncio.create_task(index.build_missing())
                    logger.info(f"Daten-Ordner geändert: {cc.CACHE_DIR}")
            for cid in resp.get("cancel_ids") or []:
                for jobs in (bt.JOBS, opt.JOBS, rlab.JOBS):
                    if cid in jobs:
                        jobs[cid]["cancel"] = True
            job = resp.get("job")
            if job:
                kind = job.get("kind")
                logger.info(f"Neuer Job: {kind} {job['job_id']}")
                if kind == "backtest":
                    active[job["job_id"]] = asyncio.create_task(handle_backtest(api, job, index))
                elif kind == "optimizer":
                    active[job["job_id"]] = asyncio.create_task(handle_optimizer(api, job, index))
                elif kind == "regime_lab":
                    active[job["job_id"]] = asyncio.create_task(handle_regime_lab(api, job, index))
                elif kind and kind.startswith("data_"):
                    active_data[job["job_id"]] = asyncio.create_task(handle_data_job(api, job, index))
            if api.bad_responses >= 3:
                await api.reset()
            fails = 0
            await asyncio.sleep(2)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            fails += 1
            logger.warning(f"Verbindung zum Server fehlgeschlagen ({e}) – "
                           f"neuer Versuch in 5s")
            first = True
            if fails >= 3:
                await api.reset()
                fails = 0
            await asyncio.sleep(5)
        except Exception:
            fails += 1
            logger.exception("Unerwarteter Fehler in der Hauptschleife")
            if fails >= 3:
                await api.reset()
                fails = 0
            await asyncio.sleep(5)


def main():
    p = argparse.ArgumentParser(description="Lokaler Backtest-/Optimizer-Worker")
    p.add_argument("--server", help="URL der Website, z.B. https://meine-app.onrender.com")
    p.add_argument("--token", help="Worker-Token aus der Website")
    p.add_argument("--data-dir", help="Ordner für lokale Kerzendaten")
    p.add_argument("--name", help="Anzeigename dieses Rechners")
    args = p.parse_args()
    cfg = load_config(args)
    setup_modules(cfg)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        print("\nWorker beendet.")


if __name__ == "__main__":
    main()
