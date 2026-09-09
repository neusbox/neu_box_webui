from __future__ import annotations

import json
import logging
import time

import requests

from neu_box_webui.master.services.nodes_pool import Nodes, Nodes_Pool


class _StatusResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "status": "online",
            "total_devices": 8,
            "idle_devices": 2,
        }


def test_query_all_status_survives_concurrent_node_pool_replacement(
    monkeypatch,
):
    pool = Nodes_Pool()
    old_node = Nodes("old-id", "old", "127.0.0.1", 59075)
    new_node = Nodes("new-id", "new", "127.0.0.2", 59075)
    pool.add_node(old_node)

    def replace_pool_during_request(*_args, **_kwargs):
        # 模拟配置同步在批量查询取得旧 ID 后原子替换整个节点字典。
        with pool._nodes_lock:
            pool.nodes = {new_node.node_id: new_node}
        return _StatusResponse()

    monkeypatch.setattr(pool, "_request", replace_pool_during_request)

    results = pool.query_all_nodes_status()

    assert results[old_node.node_id]["status"] == "online"
    assert [item["node_id"] for item in pool.get_all_nodes()] == ["new-id"]


def test_worker_http_session_ignores_environment_proxies(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    pool = Nodes_Pool()

    session = pool._get_http_session()
    try:
        assert session.trust_env is False
    finally:
        session.close()


def test_worker_request_closes_short_lived_session(monkeypatch):
    """线程池任务结束后必须确定关闭连接池，不能积累 CLOSE_WAIT。"""
    pool = Nodes_Pool()
    response = object()

    class FakeSession:
        trust_env = True
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def request(self, *_args, **_kwargs):
            return response

    session = FakeSession()
    monkeypatch.setattr(requests, "Session", lambda: session)

    assert pool._request("GET", "http://127.0.0.1/status") is response
    assert session.trust_env is False
    assert session.closed is True


def test_failed_status_query_exposes_offline_reason(monkeypatch):
    pool = Nodes_Pool()
    node = Nodes("node-id", "node", "192.0.2.1", 59075)
    pool.add_node(node)

    def fail_request(*_args, **_kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(pool, "_request", fail_request)

    result = pool.query_node_status(node.node_id)

    assert result["details"] == "connection refused"
    summary = pool.get_all_nodes()[0]
    assert summary["status"] == "offline"
    assert summary["status_error"] == "connection refused"


def test_query_all_nodes_status_queries_concurrently(monkeypatch):
    """慢节点不能串行拖死状态查询（总耗时 ≈ 最慢单节点，而非 N × 超时）。

    两个慢节点各睡 1.2s：串行 ≈ 2.4s，并发 ≈ 1.2s。
    """
    pool = Nodes_Pool()
    slow1 = Nodes("slow1", "slow1", "127.0.0.1", 59076)
    slow2 = Nodes("slow2", "slow2", "127.0.0.1", 59077)
    fast = Nodes("fast", "fast", "127.0.0.1", 59078)
    pool.add_node(slow1)
    pool.add_node(slow2)
    pool.add_node(fast)

    def fake_request(method, url, **kwargs):
        if ":59076" in url or ":59077" in url:
            time.sleep(1.2)          # 模拟慢节点
        return _StatusResponse()

    monkeypatch.setattr(pool, "_request", fake_request)

    t0 = time.monotonic()
    results = pool.query_all_nodes_status()
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, f"应并发查询，实际耗时 {elapsed:.2f}s（串行应≈2.4s）"
    assert results["slow1"]["status"] == "online"
    assert results["slow2"]["status"] == "online"
    assert results["fast"]["status"] == "online"


def test_invalid_config_read_keeps_existing_nodes(
    tmp_path,
    caplog,
):
    config = tmp_path / "nodes.json"
    config.write_text(
        json.dumps({
            "nodes_pool": [
                {"name": "node1", "host": "127.0.0.1", "port": 59075},
            ],
        }),
        encoding="utf-8",
    )
    pool = Nodes_Pool()
    pool._config_path = str(config)
    pool.sync_from_config()
    original = pool.get_all_nodes()

    config.write_text('{"nodes_pool": [', encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        pool.sync_from_config()

    assert pool.get_all_nodes() == original
    assert "读取节点配置失败，保留现有节点" in caplog.text

    # 合法的空配置仍应明确清空节点池。
    config.write_text('{"nodes_pool": []}', encoding="utf-8")
    pool.sync_from_config()
    assert pool.get_all_nodes() == []
