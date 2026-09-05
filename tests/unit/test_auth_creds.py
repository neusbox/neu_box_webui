"""注册、节点凭据（用户名+密码）、任务归属标签映射测试。

使用 fake worker（复用 test_permissions 的 FakeWorker）模拟 worker。
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
from test_permissions import _FakeResponse, _login, FakeWorker

NODE_ID = "node-1"
NODE_NAME = "gpu-01"
PASSWORD = "pass1234"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    database = tmp_path / "master.db"
    nodes = tmp_path / "nodes.json"
    nodes.write_text(json.dumps({
        "nodes_pool": [{"name": NODE_NAME, "host": "127.0.0.1", "port": 50000}],
    }), encoding="utf-8")
    migrate_database(database, MIGRATIONS_PACKAGE, REQUIRED_COLUMNS,
                     REQUIRED_INDEXES)
    monkeypatch.setenv("NEU_BOX_DB_PATH", str(database))
    monkeypatch.setenv("NEU_BOX_NODES_CONFIG", str(nodes))
    monkeypatch.setenv("NEU_BOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("NEU_BOX_EXPERIMENT_LOG_DIR", str(tmp_path / "exp-logs"))
    monkeypatch.setenv("NEU_BOX_LOGS_SHARED", "0")
    monkeypatch.setenv("NEU_BOX_ALLOW_REGISTRATION", "1")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")

    database_obj = Database(str(database))
    Database._instance = database_obj
    for name, role in (("alice", "user"), ("bob", "user"), ("root", "admin")):
        database_obj.create_user(name, PASSWORD, role=role)

    worker = FakeWorker()
    pool = Nodes_Pool()
    node = Nodes(NODE_ID, NODE_NAME, "127.0.0.1", 50000)
    node.status = "online"
    pool.nodes = {NODE_ID: node}
    pool._request = worker.request
    Nodes_Pool._instance = pool

    from neu_box_webui.master.app import create_app

    app = create_app()
    yield {"client": app.test_client(), "worker": worker, "db": database_obj}
    Nodes_Pool._instance = None


# ═══════════════════════════════════════════════════════════════
# 注册
# ═══════════════════════════════════════════════════════════════

def test_register_success_and_auto_login(env):
    client = env["client"]
    resp = client.post("/auth/register",
                       json={"username": "charlie", "password": "charlie1"})
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["user"]["role"] == "user"

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.get_json()["user"]["username"] == "charlie"


def test_register_duplicate_username(env):
    resp = env["client"].post(
        "/auth/register", json={"username": "alice", "password": "x1234"})
    assert resp.status_code == 409


def test_register_validation(env):
    client = env["client"]
    assert client.post("/auth/register",
                       json={"username": "ab", "password": "x1234"}
                       ).status_code == 400
    assert client.post("/auth/register",
                       json={"username": "valid_name", "password": "123"}
                       ).status_code == 400
    assert client.post("/auth/register",
                       json={"username": "bad name!", "password": "x1234"}
                       ).status_code == 400


def test_register_can_be_disabled(env, monkeypatch):
    monkeypatch.setenv("NEU_BOX_ALLOW_REGISTRATION", "0")
    client = env["client"]
    assert client.get("/auth/register").get_json() == {"allowed": False}
    assert client.post("/auth/register",
                       json={"username": "dave", "password": "x1234"}
                       ).status_code == 403


# ═══════════════════════════════════════════════════════════════
# 节点凭据
# ═══════════════════════════════════════════════════════════════

def _save_cred(client, node_name, username, password=None):
    body = {"username": username}
    if password is not None:
        body["password"] = password
    return client.put(f"/auth/credentials/{node_name}", json=body)


def test_credentials_save_and_list_hides_password(env):
    client = env["client"]
    _login(client, "alice")
    assert _save_cred(client, NODE_NAME, "al", "nodepw123").status_code == 200

    creds = client.get("/auth/credentials").get_json()["credentials"]
    assert creds == [{
        "node_name": NODE_NAME,
        "username": "al",
        "created_at": creds[0]["created_at"],
        "updated_at": creds[0]["updated_at"],
        "has_password": True,
    }]
    assert "password" not in creds[0]


def test_credentials_reveal_owner_only(env):
    client = env["client"]
    _login(client, "alice")
    _save_cred(client, NODE_NAME, "al", "nodepw123")

    resp = client.get(f"/auth/credentials/{NODE_NAME}/password")
    assert resp.get_json() == {"password": "nodepw123"}

    # bob 没有该节点凭据 → 404
    _login(client, "bob")
    assert client.get(f"/auth/credentials/{NODE_NAME}/password").status_code == 404


def test_credentials_update_username_keeps_password(env):
    client = env["client"]
    _login(client, "alice")
    _save_cred(client, NODE_NAME, "al", "nodepw123")

    # 不带 password → 保持旧密码
    _save_cred(client, NODE_NAME, "al2")
    assert client.get(f"/auth/credentials/{NODE_NAME}/password").get_json() \
        == {"password": "nodepw123"}

    # 空串 → 清除
    _save_cred(client, NODE_NAME, "al2", "")
    assert client.get(f"/auth/credentials/{NODE_NAME}/password").get_json() \
        == {"password": None}


def test_credentials_per_user_isolation(env):
    client = env["client"]
    _login(client, "alice")
    _save_cred(client, NODE_NAME, "al", "nodepw123")

    _login(client, "bob")
    assert client.get("/auth/credentials").get_json()["credentials"] == []


def test_credentials_delete(env):
    client = env["client"]
    _login(client, "alice")
    _save_cred(client, NODE_NAME, "al", "nodepw123")
    assert client.delete(f"/auth/credentials/{NODE_NAME}").status_code == 200
    assert client.delete(f"/auth/credentials/{NODE_NAME}").status_code == 404


def test_credentials_rejects_bad_username(env):
    client = env["client"]
    _login(client, "alice")
    assert _save_cred(client, NODE_NAME, "x").status_code == 400


# ═══════════════════════════════════════════════════════════════
# 任务归属标签：凭据用户名 ↔ WebUI 用户名
# ═══════════════════════════════════════════════════════════════

def test_submit_uses_credential_username(env):
    client, worker, db = env["client"], env["worker"], env["db"]
    _login(client, "alice")
    _save_cred(client, NODE_NAME, "al", "nodepw123")

    resp = client.post("/tasks", json={
        "node_id": NODE_ID, "command": "echo hello", "user_id": "spoofed"})
    assert resp.status_code == 202
    assert worker.submitted[-1]["user_id"] == "al"

    # 提交登记仍用 WebUI 用户名（master 侧归属）
    subs = db.list_submissions("alice")
    assert subs and subs[0]["user_name"] == "alice"


def test_submit_without_credential_uses_webui_username(env):
    client, worker = env["client"], env["worker"]
    _login(client, "alice")
    resp = client.post("/tasks", json={"node_id": NODE_ID,
                                       "command": "echo hi"})
    assert resp.status_code == 202
    assert worker.submitted[-1]["user_id"] == "alice"


def _add_cred_labeled_task(worker):
    worker.queue.append({
        "task_id": "t-al", "user_id": "al", "command": "echo cred",
        "status": "completed", "position": 2,
    })


def test_my_queue_includes_credential_label(env):
    client, worker = env["client"], env["worker"]
    _add_cred_labeled_task(worker)
    _login(client, "alice")
    _save_cred(client, NODE_NAME, "al")

    # 单节点视图：alice 的任务 = alice + al
    queue = client.get(f"/tasks/mine?node_id={NODE_ID}").get_json()["queue"]
    assert {t["task_id"] for t in queue} == {"t-alice", "t-al"}

    # 聚合视图
    agg = client.get("/tasks/mine").get_json()
    assert agg["total"] == 2

    # bob 看不到 al 的任务
    _login(client, "bob")
    queue = client.get(f"/tasks/mine?node_id={NODE_ID}").get_json()["queue"]
    assert {t["task_id"] for t in queue} == {"t-bob"}


def test_delete_uses_credential_label(env):
    client, worker = env["client"], env["worker"]
    _add_cred_labeled_task(worker)

    _login(client, "bob")
    resp = client.delete("/tasks",
                         json={"node_id": NODE_ID, "task_ids": ["t-al"]})
    assert resp.status_code == 403
    assert worker.deleted == []

    _login(client, "alice")
    _save_cred(client, NODE_NAME, "al")
    resp = client.delete("/tasks",
                         json={"node_id": NODE_ID, "task_ids": ["t-al"]})
    assert resp.status_code == 200
    assert worker.deleted == [["t-al"]]


def test_log_uses_credential_label(env):
    client, worker = env["client"], env["worker"]
    _add_cred_labeled_task(worker)

    _login(client, "bob")
    resp = client.get(f"/tasks/t-al/log?node_id={NODE_ID}")
    assert resp.status_code == 403

    _login(client, "alice")
    _save_cred(client, NODE_NAME, "al")
    resp = client.get(f"/tasks/t-al/log?node_id={NODE_ID}")
    assert resp.status_code == 200


def test_admin_sees_all_labels(env):
    client, worker = env["client"], env["worker"]
    _add_cred_labeled_task(worker)
    _login(client, "root")
    queue = client.get(f"/tasks?node_id={NODE_ID}").get_json()["queue"]
    assert {t["task_id"] for t in queue} == {"t-alice", "t-bob", "t-al"}
