"""Zentrale Konstanten & Umgebungs-Setup (aus server.py verschoben)."""
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

BERLIN = ZoneInfo("Europe/Berlin")

TOP_10_COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "POLUSDT"]
OTHER_INSTRUMENTS = [
    {"symbol": "GOLD", "yahoo": "GC=F", "name": "Gold"},
    {"symbol": "SILVER", "yahoo": "SI=F", "name": "Silver"},
    {"symbol": "OIL", "yahoo": "CL=F", "name": "Oil"},
]
OTHER_YAHOO = {i["symbol"]: i["yahoo"] for i in OTHER_INSTRUMENTS}
ALL_SYMBOLS = TOP_10_COINS + [i["symbol"] for i in OTHER_INSTRUMENTS]

POLL_INTERVAL = 12
