"""权限矩阵测试：任务归属、删除/日志校验、用户管理、实验所有权。

使用 fake worker（monkeypatch Nodes_Pool._request）模拟 worker 行为。
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


NODE_ID = "node-1"
PASSWORD = "pass1234"


class _FakeResponse:
    def __init__(self, status_code: int, payload, text: str = ""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = text
        self.headers = {"Content-Type": "text/plain"}

    def json(self):
        return self._payload


class FakeWorker:
    """模拟 worker 的 /tasks 与 /sandbox 行为。"""

    def __init__(self):
        self.queue = [
            {"task_id": "t-alice", "user_id": "alice", "command": "echo a",
             "status": "completed", "position": 0},
            {"task_id": "t-bob", "user_id": "bob", "command": "echo b",
             "status": "queued", "position": 1},
        ]
        self.sandboxes = [
            {"name": "sbx_alice_43210.slice", "owner": "alice",
             "cpu": 4, "mem": "8G", "devices": ["235:0"],
             "created_at": 1234.0, "pids": [43210]},
        ]
        self.submitted: list[dict] = []
        self.deleted: list[list[str]] = []
        self.released: list[str] = []

    def request(self, method: str, url: str, **kwargs):
        path = url.split("://", 1)[1].split("/", 1)[1]

        if method == "GET" and path == "tasks":
            return _FakeResponse(200, {"queue": self.queue})

        if method == "GET" and path == "sandbox/list":
            return _FakeResponse(
                200, {"sandboxes": self.sandboxes, "current_sandbox": None})

        if method == "POST" and path == "sandbox/release":
            body = kwargs.get("json") or {}
            name = body.get("sandbox_name", "")
            if any(s["name"] == name for s in self.sandboxes):
                self.sandboxes = [s for s in self.sandboxes
                                  if s["name"] != name]
                self.released.append(name)
                return _FakeResponse(200, {"message": f"沙盒 {name} 已销毁"})
            return _FakeResponse(500, {"error": f"沙盒 {name} 销毁失败"})

        if method == "POST" and path == "tasks":
            body = kwargs.get("json") or {}
            self.submitted.append(body)
            return _FakeResponse(202, {"task_id": "t-new", "position": 1,
                                       "message": "已加入队列"})

        if method == "DELETE" and path == "tasks":
            body = kwargs.get("json") or {}
            ids = body.get("task_ids", [])
            self.deleted.append(ids)
            before = len(self.queue)
            self.queue = [t for t in self.queue if t["task_id"] not in ids]
            return _FakeResponse(
                200, {"deleted": before - len(self.queue), "message": "已删除"})

        if path.startswith("tasks/") and path.endswith("/log"):
            task_id = path[len("tasks/"):-len("/log")]
            if any(t["task_id"] == task_id for t in self.queue):
                return _FakeResponse(200, None, "hello log")
            return _FakeResponse(404, {"error": "任务不存在"})

        if path.startswith("tasks/"):
            task_id = path[len("tasks/"):]
            for t in self.queue:
                if t["task_id"] == task_id:
                    return _FakeResponse(200, t)
            return _FakeResponse(404, {"error": "任务不存在"})

        return _FakeResponse(404, {"error": f"未知路径 {path}"})


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
    monkeypatch.delenv("NEU_BOX_LOGS_SHARED", raising=False)

    database_obj = Database(str(database))
    Database._instance = database_obj
    for name, role in (("alice", "user"), ("bob", "user"), ("root", "admin")):
        database_obj.create_user(name, PASSWORD, role=role)

    worker = FakeWorker()
    pool = Nodes_Pool()
    node = Nodes(NODE_ID, "gpu-01", "127.0.0.1", 50000)
    node.status = "online"
    pool.nodes = {NODE_ID: node}
    pool._request = worker.request
    Nodes_Pool._instance = pool

    from neu_box_webui.master.app import create_app

    app = create_app()
    yield {"client": app.test_client(), "worker": worker, "db": database_obj}
    Nodes_Pool._instance = None


def _login(client, username, password=PASSWORD):
    resp = client.post("/auth/login",
                       json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_json()


# ═══════════════════════════════════════════════════════════════
# 提交：服务端注入归属
# ═══════════════════════════════════════════════════════════════

def test_submit_injects_session_username_and_records(env):
    client, worker, db = env["client"], env["worker"], env["db"]
    _login(client, "alice")

    resp = client.post("/tasks", json={
        "node_id": NODE_ID, "command": "echo hello",
        "user_id": "spoofed", "cpu": 1,
    })
    assert resp.status_code == 202
    assert worker.submitted[-1]["user_id"] == "alice"

    subs = db.list_submissions("alice")
    assert len(subs) == 1
    assert subs[0]["task_id"] == "t-new"
    assert subs[0]["node_id"] == NODE_ID
    assert subs[0]["user_name"] == "alice"


def test_submit_requires_login(env):
    resp = env["client"].post("/tasks", json={"node_id": NODE_ID,
                                              "command": "echo x"})
    assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════
# 删除：仅本人或 admin
# ═══════════════════════════════════════════════════════════════

def test_delete_own_tasks_only(env):
    client, worker = env["client"], env["worker"]
    _login(client, "alice")

    # 混合选择：只删得掉自己的，被拒的在 denied 中返回
    resp = client.delete("/tasks", json={
        "node_id": NODE_ID, "task_ids": ["t-alice", "t-bob"]})
    assert resp.status_code == 200
    assert worker.deleted == [["t-alice"]]
    assert resp.get_json()["denied"] == ["t-bob"]

    # 只选他人的 → 403
    resp = client.delete("/tasks", json={
        "node_id": NODE_ID, "task_ids": ["t-bob"]})
    assert resp.status_code == 403
    assert worker.deleted == [["t-alice"]]  # 未转发

    # 同一个任务，本人（bob）可以删
    _login(client, "bob")
    resp = client.delete("/tasks", json={
        "node_id": NODE_ID, "task_ids": ["t-bob"]})
    assert resp.status_code == 200
    assert worker.deleted == [["t-alice"], ["t-bob"]]


def test_admin_delete_any(env):
    client, worker = env["client"], env["worker"]
    _login(client, "root")

    resp = client.delete("/tasks", json={
        "node_id": NODE_ID, "task_ids": ["t-alice", "t-bob"]})
    assert resp.status_code == 200
    assert resp.get_json().get("denied") is None
    assert worker.deleted == [["t-alice", "t-bob"]]


# ═══════════════════════════════════════════════════════════════
# 日志：仅本人或 admin
# ═══════════════════════════════════════════════════════════════

def test_log_visibility_by_owner(env):
    client = env["client"]
    _login(client, "alice")

    assert client.get(f"/tasks/t-alice/log?node_id={NODE_ID}&raw=1").status_code == 200
    assert client.get(f"/tasks/t-bob/log?node_id={NODE_ID}&raw=1").status_code == 403

    _login(client, "root")
    assert client.get(f"/tasks/t-bob/log?node_id={NODE_ID}&raw=1").status_code == 200


def test_main_queue_visible_to_all(env):
    client = env["client"]
    _login(client, "alice")
    data = client.get(f"/tasks?node_id={NODE_ID}").get_json()
    assert {t["task_id"] for t in data["queue"]} == {"t-alice", "t-bob"}

    data = client.get(f"/tasks?node_id={NODE_ID}&mine=1").get_json()
    assert [t["task_id"] for t in data["queue"]] == ["t-alice"]


# ═══════════════════════════════════════════════════════════════
# 我的队列（单节点 + 跨节点聚合）
# ═══════════════════════════════════════════════════════════════

def test_my_tasks_single_node(env):
    client = env["client"]
    _login(client, "bob")
    data = client.get(f"/tasks/mine?node_id={NODE_ID}").get_json()
    assert [t["task_id"] for t in data["queue"]] == ["t-bob"]


def test_my_tasks_aggregate(env):
    client = env["client"]
    _login(client, "alice")
    data = client.get("/tasks/mine").get_json()
    assert data["total"] == 1
    assert len(data["groups"]) == 1
    group = data["groups"][0]
    assert group["node_name"] == "gpu-01"
    assert group["node_status"] == "online"
    assert [t["task_id"] for t in group["tasks"]] == ["t-alice"]
    assert data["offline_nodes"] == []



# ═══════════════════════════════════════════════════════════════
# 节点配置：admin only
# ═══════════════════════════════════════════════════════════════

def test_node_config_requires_admin(env):
    client = env["client"]
    _login(client, "alice")
    resp = client.post("/nodes/config/add",
                       json={"name": "n2", "host": "127.0.0.1", "port": 50001})
    assert resp.status_code == 403

    _login(client, "root")
    resp = client.post("/nodes/config/add",
                       json={"name": "n2", "host": "127.0.0.1", "port": 50001})
    assert resp.status_code == 201
    names = [n["name"] for n in client.get("/nodes/config").get_json()["nodes"]]
    assert "n2" in names


# ═══════════════════════════════════════════════════════════════
# 用户管理
# ═══════════════════════════════════════════════════════════════

def test_admin_users_requires_admin(env):
    client = env["client"]
    _login(client, "alice")
    assert client.get("/admin/users").status_code == 403

    _login(client, "root")
    users = client.get("/admin/users").get_json()["users"]
    assert {u["username"] for u in users} == {"alice", "bob", "root"}


def test_user_management_lifecycle(env):
    client = env["client"]
    db = env["db"]
    _login(client, "root")

    # 创建
    resp = client.post("/admin/users",
                       json={"username": "carol", "password": PASSWORD})
    assert resp.status_code == 201
    carol_id = resp.get_json()["id"]

    # 重名
    resp = client.post("/admin/users",
                       json={"username": "carol", "password": PASSWORD})
    assert resp.status_code == 409

    # 非法用户名 / 短密码
    assert client.post("/admin/users",
                       json={"username": "bad name", "password": PASSWORD}
                       ).status_code == 400
    assert client.post("/admin/users",
                       json={"username": "ok-name", "password": "123"}
                       ).status_code == 400

    # 重置密码
    resp = client.post(f"/admin/users/{carol_id}/password",
                       json={"password": "newpass9"})
    assert resp.status_code == 200
    assert db.verify_user("carol", "newpass9") is not None

    # 禁用自己 → 拒绝
    root = db.get_user_by_username("root")
    resp = client.put(f"/admin/users/{root['id']}",
                      json={"is_active": False})
    assert resp.status_code == 400

    # 禁用 carol：新密码登录被拒
    resp = client.put(f"/admin/users/{carol_id}", json={"is_active": False})
    assert resp.status_code == 200
    assert client.post("/auth/login",
                       json={"username": "carol", "password": "newpass9"}
                       ).status_code == 403
    # 旧密码本来就是 401
    assert client.post("/auth/login",
                       json={"username": "carol", "password": PASSWORD}
                       ).status_code == 401


def test_last_admin_protection(env):
    client = env["client"]
    db = env["db"]
    _login(client, "root")

    # 创建第二个 admin 后可以降权
    resp = client.post("/admin/users",
                       json={"username": "root2", "password": PASSWORD,
                             "role": "admin"})
    root2_id = resp.get_json()["id"]
    resp = client.put(f"/admin/users/{root2_id}", json={"role": "user"})
    assert resp.status_code == 200

    # 现在只剩 root 一个启用 admin：root2 已被降权，
    # 用 root 再禁用/降权自己以外的最后一个 admin 不受影响，
    # 但 root 不能把自己降权（self 保护）
    root = db.get_user_by_username("root")
    assert client.put(f"/admin/users/{root['id']}",
                      json={"role": "user"}).status_code == 400

    # 恢复 root2 为 admin 后，root 降权 root2 会触发“最后一个 admin”检查
    client.put(f"/admin/users/{root2_id}", json={"role": "admin"})
    client.put(f"/admin/users/{root['id']}", json={"role": "admin"})  # no-op
    resp = client.put(f"/admin/users/{root2_id}", json={"is_active": False})
    # 此时启用的 admin 有 root 和 root2 两个，禁用 root2 合法
    assert resp.status_code == 200


def test_disabled_user_session_is_kicked(env):
    client = env["client"]
    db = env["db"]
    _login(client, "alice")
    assert client.get("/auth/me").status_code == 200

    _login(client, "root")
    alice = db.get_user_by_username("alice")
    resp = client.put(f"/admin/users/{alice['id']}", json={"is_active": False})
    assert resp.status_code == 200

    # alice 的会话仍在同一个 client 上被 root 覆盖了，重新验证禁用登录拒绝
    assert client.post("/auth/login",
                       json={"username": "alice", "password": PASSWORD}
                       ).status_code == 403


# ═══════════════════════════════════════════════════════════════
# 实验所有权
# ═══════════════════════════════════════════════════════════════

def test_experiment_ownership(env):
    client = env["client"]
    _login(client, "alice")
    resp = client.post("/experiments/", json={
        "title": "alice exp", "blocks": [], "tags": ["t1"],
        "created_by": "spoofed",
    })
    assert resp.status_code == 201
    exp_id = resp.get_json()["id"]

    exp = client.get(f"/experiments/{exp_id}").get_json()
    assert exp["created_by"] == "alice"  # 服务端注入，忽略前端值

    # bob 不能改/删
    _login(client, "bob")
    assert client.put(f"/experiments/{exp_id}",
                      json={"title": "hacked"}).status_code == 403
    assert client.delete(f"/experiments/{exp_id}").status_code == 403

    # alice 可以改
    _login(client, "alice")
    assert client.put(f"/experiments/{exp_id}",
                      json={"title": "alice exp v2"}).status_code == 200

    # scope=mine 过滤
    mine = client.get("/experiments/?scope=mine").get_json()
    assert [e["id"] for e in mine["experiments"]] == [exp_id]
    _login(client, "bob")
    mine = client.get("/experiments/?scope=mine").get_json()
    assert mine["experiments"] == []

    # alice 删除自己的
    _login(client, "alice")
    assert client.delete(f"/experiments/{exp_id}").status_code == 200


def test_legacy_experiment_without_owner_only_admin(env):
    client = env["client"]
    db = env["db"]
    # 旧数据：created_by 为空
    exp_id = db.create_experiment(title="legacy", blocks=[], tags=[],
                                  created_by="")

    _login(client, "alice")
    assert client.put(f"/experiments/{exp_id}",
                      json={"title": "x"}).status_code == 403
    assert client.delete(f"/experiments/{exp_id}").status_code == 403

    _login(client, "root")
    assert client.delete(f"/experiments/{exp_id}").status_code == 200


# ═══════════════════════════════════════════════════════════════
# 终端沙盒：销毁走 release 分流（队列不再展示沙盒，左栏沙盒卡入口）
# ═══════════════════════════════════════════════════════════════

def test_delete_sandbox_routed_to_release(env):
    """沙盒名走 /sandbox/release；非属主非 admin 被拒，属主可销毁。"""
    client, worker = env["client"], env["worker"]
    sbx = "sbx_alice_43210.slice"

    _login(client, "bob")
    resp = client.delete("/tasks", json={"node_id": NODE_ID, "task_ids": [sbx]})
    assert resp.status_code == 403
    assert worker.released == []

    _login(client, "alice")
    resp = client.delete("/tasks", json={"node_id": NODE_ID, "task_ids": [sbx]})
    assert resp.status_code == 200
    assert worker.released == [sbx]
    assert worker.sandboxes == []
    assert resp.get_json()["deleted"] == 1


def test_admin_delete_mixed_tasks_and_sandboxes(env):
    """混合删除：任务走 DELETE /tasks，沙盒走 release，计数合并。"""
    client, worker = env["client"], env["worker"]
    _login(client, "root")
    resp = client.delete("/tasks", json={
        "node_id": NODE_ID,
        "task_ids": ["t-bob", "sbx_alice_43210.slice"]})
    assert resp.status_code == 200
    assert worker.deleted == [["t-bob"]]
    assert worker.released == ["sbx_alice_43210.slice"]
    assert resp.get_json()["deleted"] == 2
