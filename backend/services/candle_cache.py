"""
Hybrid-Kerzen-Cache: In-Memory LRU + optionaler Disk-Fallback (/tmp).
Ziel: 90-/180-/360-Tage-Backtests und Optimizer-Läufe müssen ihre historischen
1-Minuten-Kerzen nicht bei jedem Job neu von Binance herunterladen.

Design:
- Key: symbol (immer 1m-Historie).
- Wert: List[candle] oldest-first, kontinuierlich.
- Wiederholte Requests: nur der fehlende "tail" (letzte n Minuten) wird nachgeladen,
  ältere Kerzen kommen aus dem Cache.
- Speicher-Limit über MAX_CANDLES (LRU nach Zugriffszeit). Werte übersteigen das
  Limit -> älteste Symbole werden freigegeben. Optionaler Disk-Fallback friert
  große Historien auf Platte (/tmp) ein, damit RAM klein bleibt.

Öffentliche API:
    await get_candles(session, symbol, days, job=None) -> List[Dict]
"""
import asyncio
import gzip
import logging
import os
import pickle
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Konfig via ENV (Defaults sind renderfreundlich)
# 500K Kerzen ≈ 250 MB im dict-Format -> passt für Render-Free (512 MB).
# Für größere Läufe: Disk-Cache übernimmt automatisch.
CACHE_DIR = os.environ.get("CANDLE_CACHE_DIR", "/tmp/candle_cache")
MAX_CANDLES_IN_MEMORY = int(os.environ.get("CANDLE_CACHE_MAX_CANDLES", "500000"))
DISK_ENABLED = os.environ.get("CANDLE_CACHE_DISK", "1") != "0"
TAIL_TTL_SEC = int(os.environ.get("CANDLE_CACHE_TAIL_TTL", "45"))  # Refresh-Fenster

_MEM: Dict[str, Dict] = {}  # symbol -> {"candles": [...], "last_refresh": ts, "used_at": ts}
_LOCK = asyncio.Lock()


def _disk_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}.pkl.gz")


def _ensure_dir():
    if DISK_ENABLED:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
        except OSError as e:
            logger.warning(f"candle_cache: cannot create {CACHE_DIR}: {e}")


def _load_disk(symbol: str) -> Optional[List[Dict]]:
    if not DISK_ENABLED:
        return None
    path = _disk_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rb") as f:
            data = pickle.load(f)
        if isinstance(data, list) and data:
            return data
    except (OSError, pickle.UnpicklingError, EOFError) as e:
        logger.warning(f"candle_cache disk load failed {symbol}: {e}")
    return None


def _save_disk(symbol: str, candles: List[Dict]):
    if not DISK_ENABLED or not candles:
        return
    _ensure_dir()
    path = _disk_path(symbol)
    try:
        tmp = path + ".tmp"
        with gzip.open(tmp, "wb", compresslevel=3) as f:
            pickle.dump(candles, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"candle_cache disk save failed {symbol}: {e}")


def _total_candles() -> int:
    return sum(len(v["candles"]) for v in _MEM.values())


def _evict_if_needed():
    """LRU-Eviction bis unter MAX_CANDLES_IN_MEMORY."""
    while _total_candles() > MAX_CANDLES_IN_MEMORY and _MEM:
        oldest = min(_MEM.keys(), key=lambda k: _MEM[k]["used_at"])
        entry = _MEM.pop(oldest)
        # In Disk sichern (nur wenn Disk-Cache aktiv)
        if DISK_ENABLED:
            _save_disk(oldest, entry["candles"])
        logger.info(f"candle_cache: evicted {oldest} ({len(entry['candles'])} candles) "
                    f"total_now={_total_candles()}")


def _merge_tail(existing: List[Dict], new_tail: List[Dict]) -> List[Dict]:
    """Fügt neue Kerzen ans Ende an, dedupliziert nach timestamp."""
    if not existing:
        return list(new_tail)
    if not new_tail:
        return existing
    last_ts = existing[-1]["timestamp"]
    additions = [c for c in new_tail if c["timestamp"] > last_ts]
    if not additions:
        # Möglicherweise nur die laufende Minute aktualisiert (letzte Kerze).
        first_new_ts = new_tail[0]["timestamp"] if new_tail else None
        # Ersetze evtl. die letzte Kerze wenn timestamp identisch (laufende Minute)
        replaced = list(existing)
        by_ts = {c["timestamp"]: c for c in existing[-2:]}
        for c in new_tail:
            if c["timestamp"] in by_ts:
                # ersetze in-place
                for i in range(len(replaced) - 1, max(len(replaced) - 3, -1), -1):
                    if replaced[i]["timestamp"] == c["timestamp"]:
                        replaced[i] = c
                        break
        return replaced
    return existing + additions


async def _fetch_range(session, symbol: str, start_ms: int, end_ms: int,
                       job: Dict = None) -> List[Dict]:
    """Direkt-Fetch von Binance (nur für den fehlenden Bereich)."""
    import aiohttp
    from services.backtester import BINANCE_URL, JobCancelled  # avoid cyclic top-level

    t0 = time.perf_counter()
    out: List[Dict] = []
    cur = start_ms
    span = max(end_ms - start_ms, 1)
    while cur < end_ms:
        if job is not None and job.get("cancel"):
            raise JobCancelled()
        params = {"symbol": symbol, "interval": "1m", "startTime": cur, "limit": 1000}
        data = None
        for attempt in range(4):
            try:
                async with session.get(BINANCE_URL, params=params,
                                       timeout=aiohttp.ClientTimeout(total=30)) as r:
                    data = await r.json(content_type=None)
                if isinstance(data, list):
                    break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"candle_cache fetch {symbol} attempt {attempt + 1} failed: {e}")
                data = None
            await asyncio.sleep(1.0 + attempt)
        if not isinstance(data, list) or not data:
            break
        for k in data:
            out.append({"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
        cur = data[-1][0] + 60000
        if job is not None:
            pct = min(round((cur - start_ms) / span * 100), 100)
            job["phase"] = f"Lade Daten: {symbol} ({pct}%)"
        if len(data) < 1000:
            break
        await asyncio.sleep(0.06)
    DOWNLOAD_STATS["candles"] += len(out)
    DOWNLOAD_STATS["seconds"] += time.perf_counter() - t0
    return out


async def _fetch_range_parallel(session, symbol: str, start_ms: int, end_ms: int,
                                job: Dict = None, workers: int = 3) -> List[Dict]:
    """Großen Zeitraum in Teilbereiche splitten und parallel laden (~3x schneller).
    Bei kleinen Bereichen (<2 Tage) normaler sequenzieller Fetch."""
    span = end_ms - start_ms
    if span <= 2 * 86400 * 1000 or workers <= 1:
        return await _fetch_range(session, symbol, start_ms, end_ms, job=job)
    chunk = span // workers
    bounds = [(start_ms + i * chunk,
               end_ms if i == workers - 1 else start_ms + (i + 1) * chunk)
              for i in range(workers)]
    parts = await asyncio.gather(
        *[_fetch_range(session, symbol, a, b, job=job if i == 0 else None)
          for i, (a, b) in enumerate(bounds)])
    merged: List[Dict] = []
    seen = set()
    for part in parts:
        for c in part:
            if c["timestamp"] not in seen:
                seen.add(c["timestamp"])
                merged.append(c)
    merged.sort(key=lambda c: c["timestamp"])
    return merged


async def get_candles(session, symbol: str, days: int, job: Dict = None) -> List[Dict]:
    """Liefert 1-Minuten-Kerzen der letzten `days` Tage – nutzt Cache aggressiv."""
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    async with _LOCK:
        entry = _MEM.get(symbol)
        if entry is None:
            # optional: Disk-Load
            disk = _load_disk(symbol)
            if disk:
                entry = {"candles": disk, "last_refresh": 0, "used_at": time.time()}
                _MEM[symbol] = entry
                logger.info(f"candle_cache: hydrated {symbol} from disk ({len(disk)})")

    if entry is None:
        # kompletter Fresh-Fetch (parallelisiert bei großen Zeiträumen)
        logger.info(f"candle_cache MISS {symbol} days={days}")
        candles = await _fetch_range_parallel(session, symbol, start, end, job=job)
        async with _LOCK:
            _MEM[symbol] = {"candles": candles, "last_refresh": time.time(),
                            "used_at": time.time()}
            _evict_if_needed()
        return [c for c in candles if c["timestamp"] >= start]

    # Cache-Hit: prüfe was fehlt
    cached = entry["candles"]
    cached_start = cached[0]["timestamp"] if cached else end
    cached_end = cached[-1]["timestamp"] if cached else start
    now_ts = time.time()
    needs_head = start < cached_start - 60000  # ältere Historie fehlt
    needs_tail = end > cached_end + 60000 and (now_ts - entry["last_refresh"]) > TAIL_TTL_SEC

    if not needs_head and not needs_tail:
        entry["used_at"] = now_ts
        logger.info(f"candle_cache HIT {symbol} days={days} "
                    f"(cache_span={round((cached_end-cached_start)/86400000,1)}d, hit_only)")
        return [c for c in cached if c["timestamp"] >= start]

    # Head prepend
    if needs_head:
        head = await _fetch_range_parallel(session, symbol, start, cached_start, job=job)
        # letzten Wert absägen falls == cached_start
        if head and cached and head[-1]["timestamp"] >= cached[0]["timestamp"]:
            head = [c for c in head if c["timestamp"] < cached[0]["timestamp"]]
        cached = head + cached
        logger.info(f"candle_cache EXTEND-HEAD {symbol} +{len(head)}")

    # Tail append (immer bis jetzt, um laufende Kerze zu aktualisieren)
    if needs_tail:
        tail_start = cached[-1]["timestamp"] + 60000 if cached else start
        tail = await _fetch_range(session, symbol, tail_start, end, job=job)
        cached = _merge_tail(cached, tail)
        logger.info(f"candle_cache EXTEND-TAIL {symbol} +{len(tail)}")

    async with _LOCK:
        entry["candles"] = cached
        entry["last_refresh"] = now_ts
        entry["used_at"] = now_ts
        _evict_if_needed()
    return [c for c in cached if c["timestamp"] >= start]


def stats() -> Dict:
    return {
        "symbols": len(_MEM),
        "total_candles": _total_candles(),
        "per_symbol": {k: len(v["candles"]) for k, v in _MEM.items()},
        "disk_enabled": DISK_ENABLED,
        "cache_dir": CACHE_DIR,
        "max_candles": MAX_CANDLES_IN_MEMORY,
    }


def clear():
    _MEM.clear()


# ---- Download-Zähler (für Benchmark-Statistik: Cache- vs. Netz-Kerzen) ----
DOWNLOAD_STATS = {"candles": 0, "seconds": 0.0}


def download_stats() -> Dict:
    return dict(DOWNLOAD_STATS)


# ---- Public Helfer für Daten-Verwaltung (lokaler Worker & Server) ----
def cached_meta(symbol: str) -> Optional[Dict]:
    """Metadaten des In-Memory-Eintrags (Anzahl, Zeitspanne) – None wenn leer."""
    entry = _MEM.get(symbol)
    if not entry or not entry["candles"]:
        return None
    c = entry["candles"]
    return {"candles": len(c), "first_ts": c[0]["timestamp"], "last_ts": c[-1]["timestamp"]}


def persist_symbol(symbol: str) -> bool:
    """In-Memory-Kerzen eines Symbols explizit auf Platte sichern."""
    entry = _MEM.get(symbol)
    if not entry or not entry["candles"]:
        return False
    _save_disk(symbol, entry["candles"])
    return True


def remove_symbol(symbol: str):
    """Symbol aus RAM- und Disk-Cache entfernen."""
    _MEM.pop(symbol, None)
    path = _disk_path(symbol)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning(f"candle_cache remove {symbol}: {e}")


def list_disk_symbols() -> List[Dict]:
    """Alle auf Platte gespeicherten Symbole mit Dateigröße/Änderungszeit."""
    out = []
    if not os.path.isdir(CACHE_DIR):
        return out
    for fn in sorted(os.listdir(CACHE_DIR)):
        if fn.endswith(".pkl.gz"):
            p = os.path.join(CACHE_DIR, fn)
            try:
                out.append({"symbol": fn[:-7], "bytes": os.path.getsize(p),
                            "mtime": os.path.getmtime(p)})
            except OSError:
                continue
    return out
