"""POST /nodes/get_all_nodes：默认读缓存（热路径不碰 worker），
refresh=true 才实时并发查询。

回归背景：旧实现每次请求都串行查询所有 worker（每节点 5s 超时），
前端每 8-15s 轮询一次，慢/挂起节点会把 waitress 线程池占满，
整个 WebUI 卡死（task queue depth 持续上升）。
"""

from __future__ import annotations

import json

import pytest

from neu_box_webui.database.migrations import migrate_database
from neu_box_webui.master.services.db import (
    Database,
    MIGRATIONS_PACKAGE,
    REQUIRED_COLUMNS,
    REQUIRED_INDEXES,
)
from neu_box_webui.master.services.nodes_pool import Nodes, Nodes_Pool

PASSWORD = "pass1234"


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = ""
        self.headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture()
def env(tmp_path, monkeypatch):
    database = tmp_path / "master.db"
    nodes = tmp_path / "nodes.json"
    nodes.write_text(json.dumps({
        "nodes_pool": [{"name": "gpu-01", "host": "127.0.0.1", "port": 50000}],
    }), encoding="utf-8")
    migrate_database(database, MIGRATIONS_PACKAGE, REQUIRED_COLUMNS,
                     REQUIRED_INDEXES)
    monkeypatch.setenv("NEU_BOX_DB_PATH", str(database))
    monkeypatch.setenv("NEU_BOX_NODES_CONFIG", str(nodes))
    monkeypatch.setenv("NEU_BOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("NEU_BOX_EXPERIMENT_LOG_DIR", str(tmp_path / "exp-logs"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.delenv("NEU_BOX_LOGS_SHARED", raising=False)

    database_obj = Database(str(database))
    Database._instance = database_obj
    database_obj.create_user("alice", PASSWORD)

    calls = {"status": 0, "other": 0}

    def fake_request(method, url, **kwargs):
        if url.endswith("/status"):
            calls["status"] += 1
            return _FakeResponse(200, {"status": "online", "total_devices": 2,
                                       "idle_devices": 1})
        calls["other"] += 1
        if url.endswith("/tasks"):
            return _FakeResponse(200, {"queue": []})
        return _FakeResponse(404, {"error": "unknown"})

    pool = Nodes_Pool()
    node = Nodes("node-1", "gpu-01", "127.0.0.1", 50000)
    node.status = "online"
    pool.nodes = {"node-1": node}
    pool._request = fake_request
    Nodes_Pool._instance = pool

    from neu_box_webui.master.app import create_app

    app = create_app()
    client = app.test_client()
    resp = client.post("/auth/login",
                       json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200, resp.get_json()
    yield {"client": client, "calls": calls}
    Nodes_Pool._instance = None


def test_get_all_nodes_default_uses_cache(env):
    client, calls = env["client"], env["calls"]

    resp = client.post("/nodes/get_all_nodes", json={})
    assert resp.status_code == 200
    assert len(resp.get_json()["nodes"]) == 1
    # 默认热路径：不产生任何 worker /status I/O
    assert calls["status"] == 0


def test_get_all_nodes_refresh_queries_live(env):
    client, calls = env["client"], env["calls"]

    resp = client.post("/nodes/get_all_nodes", json={"refresh": True})
    assert resp.status_code == 200
    assert calls["status"] == 1

    # query 参数形式同样生效
    resp = client.post("/nodes/get_all_nodes?refresh=1", json={})
    assert resp.status_code == 200
    assert calls["status"] == 2


def test_get_all_nodes_requires_login(env):
    from neu_box_webui.master.app import create_app  # noqa: F401

    app = create_app()
    anon = app.test_client()
    resp = anon.post("/nodes/get_all_nodes", json={})
    assert resp.status_code == 401
