"""Regime-Lab API workflow and regression coverage."""
import re
import time
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

BASE_URL = dotenv_values("/app/frontend/.env").get("REACT_APP_BACKEND_URL", "").rstrip("/")
CREDENTIALS = Path("/app/memory/test_credentials.md").read_text(encoding="utf-8")
PASSWORD_MATCH = re.search(r"Passwort\s*`([^`]+)`", CREDENTIALS)
PASSWORD = PASSWORD_MATCH.group(1) if PASSWORD_MATCH else None
STATE = {"analysis_id": None, "dynamic_id": None, "base_strategy_id": None, "analysis_job": None, "opt_job": None, "wf_job": None}


def poll_job(client, job_id, timeout=330):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"{BASE_URL}/api/regime-lab/status/{job_id}", timeout=30)
        assert response.status_code == 200, response.text
        last = response.json()
        if last.get("status") != "running":
            return last
        time.sleep(2)
    pytest.fail(f"Job {job_id} timed out after {timeout}s; last={last}")


@pytest.fixture(scope="module")
def api_client():
    assert BASE_URL, "REACT_APP_BACKEND_URL missing"
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    yield session
    if PASSWORD:
        login = session.post(f"{BASE_URL}/api/auth/login", json={"password": PASSWORD}, timeout=30)
        if login.ok:
            session.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
            if STATE.get("dynamic_id"):
                session.delete(f"{BASE_URL}/api/dynamic/{STATE['dynamic_id']}", timeout=30)
            if STATE.get("base_strategy_id"):
                session.delete(f"{BASE_URL}/api/strategies/custom/{STATE['base_strategy_id']}", timeout=30)
            if STATE.get("analysis_id"):
                session.delete(f"{BASE_URL}/api/regime-lab/{STATE['analysis_id']}", timeout=30)


@pytest.fixture(scope="module")
def admin_client(api_client):
    if not PASSWORD:
        pytest.skip("Admin password missing in test_credentials.md")
    response = api_client.post(f"{BASE_URL}/api/auth/login", json={"password": PASSWORD}, timeout=30)
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data.get("token"), str) and data["token"]
    api_client.headers.update({"Authorization": f"Bearer {data['token']}"})
    return api_client


class TestRegimeLabWorkflow:
    """Analyze → optimize → assign → walk-forward → build, plus regressions."""

    def test_01_analyze_requires_admin(self, api_client):
        response = api_client.post(f"{BASE_URL}/api/regime-lab/analyze", json={
            "symbols": ["BTCUSDT"], "timeframe": "15m", "days": 60,
        }, timeout=30)
        assert response.status_code in (401, 403), response.text
        data = response.json()
        assert data.get("detail")

    def test_02_regression_endpoints(self, api_client):
        checks = [
            ("/api/strategies", "strategies"),
            ("/api/coins", "coins"),
            ("/api/optimizer/active", "active"),
        ]
        for endpoint, key in checks:
            response = api_client.get(f"{BASE_URL}{endpoint}", timeout=30)
            assert response.status_code == 200, f"{endpoint}: {response.text}"
            data = response.json()
            assert key in data, f"{endpoint}: missing {key}"
        response = api_client.get(
            f"{BASE_URL}/api/dynamic/current-regime",
            params={"symbol": "BTCUSDT", "timeframe": "15m", "days": 30}, timeout=90,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data.get("symbol") == "BTCUSDT"
        assert isinstance(data.get("regimes"), list) and data["regimes"]
        assert isinstance(data.get("current"), dict)
        assert "regime" in data["current"]

    def test_03_seeded_analysis_schema_and_labels(self, api_client):
        response = api_client.get(f"{BASE_URL}/api/regime-lab/list", timeout=30)
        assert response.status_code == 200, response.text
        rows = response.json().get("analyses")
        assert isinstance(rows, list) and rows
        seeded = next((row for row in rows if row.get("id") == "ra_c8206904"), rows[0])
        detail_response = api_client.get(f"{BASE_URL}/api/regime-lab/{seeded['id']}", timeout=30)
        assert detail_response.status_code == 200, detail_response.text
        analysis = detail_response.json()["analysis"]
        self._validate_analysis(analysis)

    def test_04_keep_toggle_and_restore(self, admin_client):
        aid = "ra_c8206904"
        detail = admin_client.get(f"{BASE_URL}/api/regime-lab/{aid}", timeout=30)
        if detail.status_code == 404:
            pytest.skip("Seeded analysis ra_c8206904 is unavailable")
        for keep in (False, True):
            response = admin_client.post(f"{BASE_URL}/api/regime-lab/{aid}/keep", json={
                "scope": "combined", "regime_id": 0, "keep": keep,
            }, timeout=30)
            assert response.status_code == 200, response.text
            assert response.json()["kept"]["combined:0"] is keep
            saved = admin_client.get(f"{BASE_URL}/api/regime-lab/{aid}", timeout=30).json()["analysis"]
            assert saved["kept"]["combined:0"] is keep

    def test_04a_keep_rejects_missing_regime_id(self, admin_client):
        response = admin_client.post(
            f"{BASE_URL}/api/regime-lab/ra_c8206904/keep",
            json={"scope": "combined", "keep": False}, timeout=30,
        )
        assert response.status_code in (400, 422), response.text
        assert response.json().get("detail")

    def test_04b_discarded_regime_is_excluded_from_build(self, admin_client):
        aid = "ra_c8206904"
        dynamic_id = None
        strategy_id = None
        try:
            discarded = admin_client.post(f"{BASE_URL}/api/regime-lab/{aid}/keep", json={
                "scope": "combined", "regime_id": 0, "keep": False,
            }, timeout=30)
            assert discarded.status_code == 200, discarded.text
            built = admin_client.post(f"{BASE_URL}/api/regime-lab/{aid}/build", json={
                "scope": "combined", "name": "TEST_Discarded_Regime_Guard",
            }, timeout=30)
            assert built.status_code == 200, built.text
            data = built.json()
            dynamic_id = data.get("id")
            strategy_id = data.get("strategy_id")
            assert 0 not in data.get("regimes", []), data
            assert 1 in data.get("regimes", []), data
        finally:
            admin_client.post(f"{BASE_URL}/api/regime-lab/{aid}/keep", json={
                "scope": "combined", "regime_id": 0, "keep": True,
            }, timeout=30)
            if dynamic_id:
                admin_client.delete(f"{BASE_URL}/api/dynamic/{dynamic_id}", timeout=30)
            if strategy_id and strategy_id.startswith("custom_") and strategy_id != "custom_dd633f11":
                admin_client.delete(f"{BASE_URL}/api/strategies/custom/{strategy_id}", timeout=30)


    def test_05_analyze_and_running_job_guard(self, admin_client):
        active = admin_client.get(f"{BASE_URL}/api/regime-lab/active", timeout=30).json().get("active")
        assert active is None, f"Pre-existing Regime-Lab job prevents test: {active}"
        payload = {
            "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "15m", "days": 60,
            "scope": "both", "max_regimes": 4, "train_pct": 75,
            "name": "TEST_Regime_Lab_QA",
        }
        started = admin_client.post(f"{BASE_URL}/api/regime-lab/analyze", json=payload, timeout=30)
        assert started.status_code == 200, started.text
        data = started.json()
        assert data.get("status") == "started" and data.get("job_id")
        STATE["analysis_job"] = data["job_id"]

        conflict = admin_client.post(f"{BASE_URL}/api/regime-lab/analyze", json=payload, timeout=30)
        assert conflict.status_code == 409, conflict.text
        assert "läuft bereits" in conflict.json().get("detail", "")

        completed = poll_job(admin_client, data["job_id"], timeout=180)
        assert completed.get("status") == "done", completed
        assert completed.get("progress") == 100
        STATE["analysis_id"] = completed.get("result", {}).get("analysis_id")
        assert STATE["analysis_id"]

    def test_06_created_analysis_persistence_and_schema(self, admin_client):
        aid = STATE["analysis_id"]
        assert aid
        listed = admin_client.get(f"{BASE_URL}/api/regime-lab/list", timeout=30).json()["analyses"]
        row = next((r for r in listed if r.get("id") == aid), None)
        assert row and row["name"] == "TEST_Regime_Lab_QA"
        response = admin_client.get(f"{BASE_URL}/api/regime-lab/{aid}", timeout=30)
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        self._validate_analysis(analysis)
        for symbol in ("BTCUSDT", "ETHUSDT"):
            assert analysis["bounds"][symbol]["train_end_ts"]
            assert analysis["bounds"][symbol]["train_end_ts"] < analysis["bounds"][symbol]["end_ts"]

    def test_07_optimize_and_running_job_guard(self, admin_client):
        aid = STATE["analysis_id"]
        payload = {
            "scope": "combined", "regime_id": 1, "mode": "combo",
            "indicators": ["ema_slow", "rel_volume", "macd"], "iterations": 5,
            "min_trades": 5, "max_rules": 2, "optimize": {"tpsl": True},
            "regime_walk_forward": True,
        }
        started = admin_client.post(f"{BASE_URL}/api/regime-lab/{aid}/optimize", json=payload, timeout=30)
        assert started.status_code == 200, started.text
        data = started.json()
        assert data.get("status") == "started" and data.get("job_id")
        STATE["opt_job"] = data["job_id"]

        conflict = admin_client.post(f"{BASE_URL}/api/regime-lab/{aid}/optimize", json=payload, timeout=30)
        assert conflict.status_code == 409, conflict.text
        completed = poll_job(admin_client, data["job_id"], timeout=330)
        assert completed.get("status") == "done", completed
        assert completed.get("result", {}).get("top5"), completed

    def test_08_run_result_and_assign_two_regimes(self, admin_client):
        response = admin_client.get(f"{BASE_URL}/api/regime-lab/run/{STATE['opt_job']}", timeout=30)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["regime_id"] == 1
        assert result["mode"] == "combo"
        assert isinstance(result.get("discovery", {}).get("rules", []), list)
        top = result["top5"][0]
        assert isinstance(top.get("trade_params"), dict)
        assert isinstance(top.get("metrics"), dict)
        assert "trades" in top["metrics"] and "pnl" in top["metrics"]
        assert "validation" in top
        candidate = {
            "mode": result["mode"], "strategy_id": result.get("strategy_id"),
            "strategy_name": result.get("strategy_name"), "definition": result.get("definition"),
            "rules": result.get("discovery", {}).get("rules", []),
            "trade_params": top["trade_params"], "metrics": top["metrics"],
            "validation": top.get("validation"), "source_job_id": STATE["opt_job"],
        }
        for regime_id in (1, 0):
            assigned = admin_client.post(
                f"{BASE_URL}/api/regime-lab/{STATE['analysis_id']}/assign",
                json={"scope": "combined", "regime_id": regime_id, "candidate": candidate}, timeout=30,
            )
            assert assigned.status_code == 200, assigned.text
            assert f"combined:{regime_id}" in assigned.json()["assignments"]
        saved = admin_client.get(
            f"{BASE_URL}/api/regime-lab/{STATE['analysis_id']}", timeout=30
        ).json()["analysis"]
        assert all(f"combined:{rid}" in saved["assignments"] for rid in (0, 1))

    def test_09_walkforward_result_and_persistence(self, admin_client):
        started = admin_client.post(
            f"{BASE_URL}/api/regime-lab/{STATE['analysis_id']}/walkforward",
            json={"scope": "combined"}, timeout=30,
        )
        assert started.status_code == 200, started.text
        STATE["wf_job"] = started.json()["job_id"]
        completed = poll_job(admin_client, STATE["wf_job"], timeout=240)
        assert completed.get("status") == "done", completed
        result = completed["result"]
        assert isinstance(result.get("dynamic_test"), dict)
        assert "pnl" in result["dynamic_test"] and "trades" in result["dynamic_test"]
        assert isinstance(result.get("best_single"), dict)
        assert isinstance(result.get("verdict"), dict)
        assert isinstance(result.get("per_regime"), list)
        assert isinstance(result.get("points"), list)
        persisted = admin_client.get(
            f"{BASE_URL}/api/regime-lab/{STATE['analysis_id']}", timeout=30
        ).json()["analysis"]
        assert "combined" in persisted.get("walkforward", {})

    def test_10_build_dynamic_and_verify_list(self, admin_client):
        response = admin_client.post(
            f"{BASE_URL}/api/regime-lab/{STATE['analysis_id']}/build",
            json={"scope": "combined", "name": "TEST_Regime_Lab_Dynamic_QA"}, timeout=30,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data.get("status") == "success"
        STATE["dynamic_id"] = data.get("id")
        STATE["base_strategy_id"] = data.get("strategy_id")
        assert STATE["dynamic_id"] and isinstance(data.get("regimes"), list)
        listed = admin_client.get(f"{BASE_URL}/api/dynamic/list", timeout=30)
        assert listed.status_code == 200, listed.text
        created = next((d for d in listed.json()["strategies"] if d.get("id") == STATE["dynamic_id"]), None)
        assert created and created["name"] == "TEST_Regime_Lab_Dynamic_QA"
        assert created["settings"]["source"] == "regime_lab"
        assert created["settings"]["analysis_id"] == STATE["analysis_id"]

    @staticmethod
    def _validate_analysis(analysis):
        assert analysis.get("combined", {}).get("model", {}).get("regimes")
        regimes = analysis["combined"]["model"]["regimes"]
        for regime in regimes:
            label = regime.get("label", "")
            strength = regime.get("stats", {}).get("trend_strength")
            assert strength is not None
            if strength >= 1.0:
                assert "Seitwärtsmarkt" not in label, regime
            if strength < 0.5:
                assert "Seitwärtsmarkt" in label, regime
        for symbol in analysis.get("symbols", []):
            assert analysis["combined"]["per_symbol"][symbol]["segments"]
            assert analysis["per_coin"][symbol]["model"]["regimes"]
            assert analysis["per_coin"][symbol]["segments"]
            assert analysis["chart"][symbol]
        assert isinstance(analysis["combined"].get("coin_similarity"), list)
