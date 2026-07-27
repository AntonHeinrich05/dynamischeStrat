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

WORKER_VERSION = "1.3.2"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "worker_config.json"

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
    os.environ["CANDLE_CACHE_MAX_CANDLES"] = str(int(cfg.get("ram_limit_mb", 4096)) * 2000)
    os.environ["CANDLE_CACHE_DISK"] = "1"
    # Multi-Core: 0 = alle Kerne (wird per Website-Einstellung überschrieben)
    os.environ.setdefault("SIM_WORKERS", "0")


# nach setup_modules() importiert (siehe main)
bt = opt = cc = registry_mod = None


def _import_services():
    global bt, opt, cc, registry_mod
    from services import backtester as _bt
    from services import optimizer as _opt
    from services import candle_cache as _cc
    from strategies import registry as _reg
    bt, opt, cc, registry_mod = _bt, _opt, _cc, _reg


# ---------------- Daten-Index (Inventar für die Website) ----------------
class DataIndex:
    def __init__(self):
        self.path = Path(cc.CACHE_DIR) / "index.json"
        self.meta = {}
        try:
            self.meta = json.loads(self.path.read_text())
        except (ValueError, OSError):
            self.meta = {}

    def _save(self):
        try:
            self.path.write_text(json.dumps(self.meta, indent=1))
        except OSError:
            pass

    def update_from_cache(self, symbol):
        m = cc.cached_meta(symbol)
        if not m:
            return
        f = Path(cc.CACHE_DIR) / f"{symbol}.pkl.gz"
        self.meta[symbol] = {**m, "bytes": f.stat().st_size if f.exists() else 0,
                             "updated": datetime.now(timezone.utc).isoformat()}
        self._save()

    def remove(self, symbol):
        self.meta.pop(symbol, None)
        self._save()

    async def build_missing(self):
        """Vorhandene Daten-Dateien ohne Index-Eintrag im Hintergrund indizieren."""
        for row in cc.list_disk_symbols():
            sym = row["symbol"]
            if sym in self.meta and self.meta[sym].get("bytes") == row["bytes"]:
                continue
            try:
                candles = await asyncio.to_thread(cc._load_disk, sym)
                if candles:
                    self.meta[sym] = {"candles": len(candles),
                                      "first_ts": candles[0]["timestamp"],
                                      "last_ts": candles[-1]["timestamp"],
                                      "bytes": row["bytes"],
                                      "updated": datetime.now(timezone.utc).isoformat()}
            except Exception as e:
                logger.warning(f"Index {sym}: {e}")
        self._save()

    def summary(self):
        disk = shutil.disk_usage(cc.CACHE_DIR)
        symbols = []
        on_disk = {r["symbol"]: r for r in cc.list_disk_symbols()}
        for sym, r in on_disk.items():
            m = self.meta.get(sym, {})
            symbols.append({"symbol": sym, "bytes": r["bytes"],
                            "candles": m.get("candles"), "first_ts": m.get("first_ts"),
                            "last_ts": m.get("last_ts"), "updated": m.get("updated")})
        return {"dir": cc.CACHE_DIR, "total_bytes": sum(s["bytes"] for s in symbols),
                "disk_free_gb": round(disk.free / 1e9, 1), "symbols": symbols}


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
    def __init__(self, session, server):
        self.session = session
        self.server = server

    async def post(self, path, payload, compress=False):
        import aiohttp
        url = f"{self.server}{path}"
        if compress:
            data = gzip.compress(json.dumps(payload).encode())
            headers = {"Content-Type": "application/json", "Content-Encoding": "gzip"}
            async with self.session.post(url, data=data, headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=300)) as r:
                return r.status, await r.json(content_type=None)
        async with self.session.post(url, json=payload,
                                     timeout=aiohttp.ClientTimeout(total=60)) as r:
            return r.status, await r.json(content_type=None)


# ---------------- Job-Ausführung (identischer Code wie der Server) ----------------
def _mk_job(jobs_dict, job_id):
    jobs_dict[job_id] = {"id": job_id, "status": "running", "progress": 0,
                         "phase": "Startet (lokal)", "params": {}, "cancel": False,
                         "created_at": datetime.now(timezone.utc).isoformat(),
                         "result": None, "error": None}
    return jobs_dict[job_id]


async def _relay_progress(api, job_id, job, extra_best=False):
    """Fortschritt an den Server melden bis der Job fertig ist; Abbruch übernehmen."""
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


async def handle_backtest(api, job_spec, index):
    job_id = job_spec["job_id"]
    a = job_spec["payload"]["args"]
    registry_mod.registry.load_custom(job_spec["payload"].get("custom_definitions") or [])
    job = _mk_job(bt.JOBS, job_id)
    logger.info(f"Backtest {job_id}: {a['strategy_ids']} auf {a['symbols']} ({a['days']} Tage)")
    # Rechnung in eigenem Thread/Loop -> Poll-Heartbeat bleibt aktiv.
    task = asyncio.create_task(_run_isolated(lambda: bt.run_backtest(
        job_id, a["strategy_ids"], a["symbols"], a["days"], a["cfg"],
        registry_mod.registry, a["settings"], None, a.get("strategy_configs"),
        a.get("default_timeframe"), a.get("date_from"), a.get("date_to"))))
    relay = asyncio.create_task(_relay_progress(api, job_id, job))
    await task
    relay.cancel()
    payload = {"kind": "backtest", "status": job["status"], "error": job["error"],
               "result": job["result"],
               "export_trades": (job.get("export_trades") or [])[:50000]}
    await api.post(f"/api/worker/job/{job_id}/result", payload, compress=True)
    for sym in a["symbols"]:  # frisch geladene Kerzen dauerhaft speichern
        if await cc.persist_symbol_async(sym):
            await asyncio.to_thread(index.update_from_cache, sym)
    bt.JOBS.pop(job_id, None)
    logger.info(f"Backtest {job_id} fertig: {job['status']}")


async def handle_optimizer(api, job_spec, index):
    job_id = job_spec["job_id"]
    a = job_spec["payload"]["args"]
    registry_mod.registry.load_custom(job_spec["payload"].get("custom_definitions") or [])
    job = _mk_job(opt.JOBS, job_id)
    job["best"] = None
    body = a["body"]
    logger.info(f"Optimizer {job_id}: mode={body.get('mode')} auf {body.get('symbols')}")
    # Rechnung in eigenem Thread/Loop -> Poll-Heartbeat bleibt aktiv.
    task = asyncio.create_task(_run_isolated(lambda: opt.run_optimizer(
        job_id, body, registry_mod.registry, a["settings"], a["default_cfg"], None)))
    relay = asyncio.create_task(_relay_progress(api, job_id, job, extra_best=True))
    await task
    relay.cancel()
    payload = {"kind": "optimizer", "status": job["status"], "error": job["error"],
               "result": job["result"], "best": job.get("best"),
               "export_trades": (job.get("export_trades") or [])[:25000]}
    await api.post(f"/api/worker/job/{job_id}/result", payload, compress=True)
    for sym in (body.get("symbols") or []):
        if await cc.persist_symbol_async(sym):
            await asyncio.to_thread(index.update_from_cache, sym)
    opt.JOBS.pop(job_id, None)
    logger.info(f"Optimizer {job_id} fertig: {job['status']}")


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
            async with aiohttp.ClientSession(headers=headers) as data_session:
                for i, sym in enumerate(symbols):
                    if local["cancel"]:
                        status = "cancelled"
                        break
                    await report(round(i / len(symbols) * 100), f"Lade {sym} ({days_map[sym]} Tage)...")
                    try:
                        await cc.get_candles(data_session, sym, days_map[sym], job=local)
                        await cc.persist_symbol_async(sym)
                        await asyncio.to_thread(index.update_from_cache, sym)
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
    payload = {"kind": kind, "status": status,
               "error": "; ".join(errors)[:300] if (errors and status != "done") else None,
               "summary": {"symbols": done_syms, "errors": errors,
                           "data": index.summary()}}
    await api.post(f"/api/worker/job/{job_id}/result", payload)
    logger.info(f"Daten-Job {job_id} ({kind}) fertig: {status} {done_syms}")


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

    session = aiohttp.ClientSession(headers={"X-Worker-Token": cfg["token"]})
    api = Api(session, cfg["server"])
    logger.info(f"Worker '{cfg['name']}' verbindet zu {cfg['server']} · Daten: {cc.CACHE_DIR}")
    first = True
    while True:
        try:
            for d in (active, active_data):
                for jid in [j for j, t in d.items() if t.done()]:
                    d.pop(jid)
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
                    cc.MAX_CANDLES_IN_MEMORY = int(server_settings["ram_limit_mb"]) * 2000
                nd = (server_settings.get("data_dir") or "").strip()
                if nd and nd != cc.CACHE_DIR:
                    Path(nd).expanduser().mkdir(parents=True, exist_ok=True)
                    cc.CACHE_DIR = str(Path(nd).expanduser())
                    cc.clear()
                    index.__init__()
                    asyncio.create_task(index.build_missing())
                    logger.info(f"Daten-Ordner geändert: {cc.CACHE_DIR}")
            for cid in resp.get("cancel_ids") or []:
                for jobs in (bt.JOBS, opt.JOBS):
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
                elif kind and kind.startswith("data_"):
                    active_data[job["job_id"]] = asyncio.create_task(handle_data_job(api, job, index))
            await asyncio.sleep(2)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            logger.warning(f"Verbindung zum Server fehlgeschlagen ({e}) – neuer Versuch in 5s")
            first = True
            await asyncio.sleep(5)
        except Exception:
            logger.exception("Unerwarteter Fehler in der Hauptschleife")
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
