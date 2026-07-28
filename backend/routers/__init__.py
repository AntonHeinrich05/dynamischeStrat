"""Alle API-Router. Neue Bereiche hier registrieren."""
from routers import auth, general, ws, analytics, strategies, backtest, optimizer, \
    autotrade, control, ai, local_worker, macro, dynamic, regime_lab

ALL_ROUTERS = [
    auth.router,
    general.router,
    ws.router,
    analytics.router,
    strategies.router,
    backtest.router,
    optimizer.router,
    autotrade.router,
    control.router,
    ai.router,
    local_worker.router,
    macro.router,
    dynamic.router,
    regime_lab.router,
]
