from __future__ import annotations

import json
import logging

import pytest

from neu_box_webui import API_VERSION
from neu_box_webui.database.migrations import migrate_database
from neu_box_webui.master.services.db import (
    Database as MasterDatabase,
    MIGRATIONS_PACKAGE as MASTER_MIGRATIONS,
    REQUIRED_COLUMNS as MASTER_COLUMNS,
    REQUIRED_INDEXES as MASTER_INDEXES,
)


def test_master_app_health_and_packaged_static(tmp_path, monkeypatch):
    database = tmp_path / "master.db"
    nodes = tmp_path / "nodes.json"
    nodes.write_text(json.dumps({"nodes_pool": []}), encoding="utf-8")
    migrate_database(
        database,
        MASTER_MIGRATIONS,
        MASTER_COLUMNS,
        MASTER_INDEXES,
    )
    monkeypatch.setenv("NEU_BOX_DB_PATH", str(database))
    monkeypatch.setenv("NEU_BOX_NODES_CONFIG", str(nodes))
    monkeypatch.setenv("NEU_BOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("NEU_BOX_EXPERIMENT_LOG_DIR", str(tmp_path / "exp-logs"))
    MasterDatabase._instance = None

    from neu_box_webui.master.app import create_app

    client = create_app().test_client()
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json["role"] == "master"
    assert health.json["api_version"] == API_VERSION
    assert health.json["schema_version"] == 1
    assert client.get("/").status_code == 200


def test_node_apply_status_tracks_worker_api_version():
    from neu_box_webui.master.services.nodes_pool import Nodes

    node = Nodes("n1", "node1", "127.0.0.1", 59075)
    assert node.worker_api_version is None

    # 旧 worker（无字段）: 保持 None，不告警
    node.apply_status({"status": "online"})
    assert node.worker_api_version is None

    # 匹配: 记录，不告警
    node.apply_status({"status": "online", "api_version": API_VERSION})
    assert node.worker_api_version == API_VERSION

    # 低版本: 记录并告警
    node.apply_status({"status": "online", "api_version": 0})
    assert node.worker_api_version == 0
    assert node.worker_api_version != API_VERSION


def test_node_apply_status_warns_on_api_mismatch(caplog):
    from neu_box_webui.master.services.nodes_pool import Nodes

    node = Nodes("n1", "node1", "127.0.0.1", 59075)
    with caplog.at_level(logging.WARNING):
        node.apply_status({"status": "online", "api_version": 0})
    assert any("API 版本不匹配" in record.message for record in caplog.records)
