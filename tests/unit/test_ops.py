"""Rroot 运维工单：审批、单次 root 执行、知识候选与 FTS 检索。

Fake worker 模拟 worker /tasks 的提交、轮询与日志接口；执行器使用
NEUOPS_OPS_EXECUTE_SYNC=1，便于在测试中同步验证结果。
"""

from __future__ import annotations

import json
import re

import pytest

from neuops.database.migrations import migrate_database
from neuops.master.services.db import (
    Database,
    MIGRATIONS_PACKAGE,
    REQUIRED_COLUMNS,
    REQUIRED_INDEXES,
)
from neuops.master.services.nodes_pool import Nodes, Nodes_Pool

NODE_ID = "node-ops"
PASSWORD = "pass1234"
_MARKER_RE = re.compile(r"(__NEUOPS_[0-9a-f]+__)")


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = text
        self.headers = {"Content-Type": "text/plain"}

    def json(self):
        return self._payload


class FakeOpsWorker:
    def __init__(self):
        self.submitted: list[dict] = []
        self.tasks: dict[str, dict] = {}
        self.logs: dict[str, str] = {}
        self.sequence = 0

    def request(self, method: str, url: str, **kwargs):
        path = url.split("://", 1)[1].split("/", 1)[1]
        if method == "POST" and path == "tasks":
            body = kwargs.get("json") or {}
            self.submitted.append(body)
            self.sequence += 1
            task_id = f"task-{self.sequence}"
            marker = _MARKER_RE.search(body.get("command", ""))
            marker_text = marker.group(1) if marker else "NO_MARKER"
            stdout = "hello out"
            stderr = "warn err"
            self.logs[task_id] = (
                f"noise before\n"
                f"{marker_text}_STDOUT_BEGIN\n{stdout}\n"
                f"{marker_text}_STDOUT_END\n"
                f"{marker_text}_STDERR_BEGIN\n{stderr}\n"
                f"{marker_text}_STDERR_END\n"
            )
            self.tasks[task_id] = {
                "task_id": task_id,
                "status": "completed",
                "result": {"returncode": 0, "timed_out": False,
                           "error": None},
            }
            return _FakeResponse(202, {"task_id": task_id, "position": 1})
        if method == "GET" and path.startswith("tasks/") and path.endswith("/log"):
            task_id = path[len("tasks/"):-len("/log")]
            return _FakeResponse(200, None, self.logs.get(task_id, ""))
        if method == "GET" and path.startswith("tasks/"):
            task_id = path[len("tasks/"):]
            task = self.tasks.get(task_id)
            if not task:
                return _FakeResponse(404, {"error": "任务不存在"})
            return _FakeResponse(200, task)
        return _FakeResponse(404, {"error": f"未知路径 {path}"})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    database = tmp_path / "master.db"
    nodes = tmp_path / "nodes.json"
    nodes.write_text(json.dumps({
        "nodes_pool": [{"name": "ops-01", "host": "127.0.0.1", "port": 50000}],
    }), encoding="utf-8")
    migrate_database(database, MIGRATIONS_PACKAGE, REQUIRED_COLUMNS,
                     REQUIRED_INDEXES)
    monkeypatch.setenv("NEU_BOX_DB_PATH", str(database))
    monkeypatch.setenv("NEU_BOX_NODES_CONFIG", str(nodes))
    monkeypatch.setenv("NEU_BOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("NEU_BOX_EXPERIMENT_LOG_DIR", str(tmp_path / "exp-logs"))
    monkeypatch.setenv("NEUOPS_OPS_LOG_DIR", str(tmp_path / "ops-logs"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("NEUOPS_OPS_EXECUTE_SYNC", "1")
    monkeypatch.setenv("NEUOPS_OPS_LEARN_SYNC", "1")
    monkeypatch.setenv("NEUOPS_OPS_AUTO_LEARN", "1")
    monkeypatch.setenv("NEUOPS_AI_ENABLED", "0")
    monkeypatch.delenv("NEUOPS_AI_API_KEY", raising=False)

    db = Database(str(database))
    Database._instance = db
    for name, role in (("alice", "user"), ("root", "admin")):
        db.create_user(name, PASSWORD, role=role)

    worker = FakeOpsWorker()
    pool = Nodes_Pool()
    node = Nodes(NODE_ID, "ops-01", "127.0.0.1", 50000)
    node.status = "online"
    pool.nodes = {NODE_ID: node}
    pool._request = worker.request
    Nodes_Pool._instance = pool

    from neuops.master.app import create_app

    app = create_app()
    yield {"client": app.test_client(), "worker": worker, "db": db}
    Nodes_Pool._instance = None


def _login(client, username):
    resp = client.post("/auth/login",
                       json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.get_json()


def _create_ticket(client, **overrides):
    body = {
        "node_id": NODE_ID,
        "title": "检查磁盘",
        "request": "查看 /var 剩余空间",
        "command": "df -h /var",
        **overrides,
    }
    return client.post("/ops/tickets", json=body)


def test_user_cannot_approve_or_execute(env):
    client = env["client"]
    _login(client, "alice")
    resp = _create_ticket(client)
    assert resp.status_code == 201, resp.get_json()
    ticket_id = resp.get_json()["ticket"]["id"]

    assert client.post(f"/ops/tickets/{ticket_id}/approve",
                       json={"reason": "ok", "execute": True}
                       ).status_code == 403
    assert client.post(f"/ops/tickets/{ticket_id}/execute"
                       ).status_code == 403
    assert env["worker"].submitted == []


def test_admin_approve_executes_once_and_returns_stdout_stderr(env):
    client, worker = env["client"], env["worker"]
    _login(client, "alice")
    ticket_id = _create_ticket(client).get_json()["ticket"]["id"]
    _login(client, "root")

    resp = client.post(f"/ops/tickets/{ticket_id}/approve", json={
        "reason": "低风险，允许执行",
        "execute": True,
    })
    assert resp.status_code == 200, resp.get_json()
    data = client.get(f"/ops/tickets/{ticket_id}").get_json()
    assert data["status"] == "succeeded"
    assert data["exec_result"]["returncode"] == 0
    assert data["exec_result"]["stdout"] == "hello out"
    assert data["exec_result"]["stderr"] == "warn err"
    assert data["exec_result"]["markers_found"] is True
    assert data["approval_snapshot"]["command"] == "df -h /var"
    assert data["approval_snapshot"]["target"]["node_id"] == NODE_ID
    assert data["approval_snapshot"]["exec_env"]["user"] == "root"

    # 固定 root 身份，命令经过包装后交给 worker。
    assert worker.submitted[-1]["user_id"] == "root"
    assert worker.submitted[-1]["target"] == {"type": "host"}
    assert "df -h /var" not in worker.submitted[-1]["command"]
    assert "__NEUOPS_" in worker.submitted[-1]["command"]

    # 单次授权：再次执行被拒绝。
    assert client.post(f"/ops/tickets/{ticket_id}/execute"
                       ).status_code == 400
    assert len(worker.submitted) == 1

    # 批准后不可修改（服务端状态机 + 数据库触发器双保险）。
    assert client.put(f"/ops/tickets/{ticket_id}",
                      json={"command": "rm -rf /", "reason": "x"}
                      ).status_code == 403


def test_reject_creates_pending_knowledge_ticket_and_approval_adds_list(env):
    client = env["client"]
    _login(client, "alice")
    ticket_id = _create_ticket(client, command="rm -rf /var/tmp/cache").get_json()[
        "ticket"]["id"]
    _login(client, "root")

    resp = client.post(f"/ops/tickets/{ticket_id}/reject",
                       json={"reason": "命令范围过大，需改为精确路径"})
    assert resp.status_code == 200, resp.get_json()
    assert client.get(f"/ops/tickets/{ticket_id}").get_json()["status"] == "rejected"

    # 自学习候选以知识工单形式出现，等待管理员审批。
    tickets = client.get("/ops/tickets?kind=knowledge").get_json()["tickets"]
    candidates = [t for t in tickets if t["source_ticket_id"] == ticket_id]
    assert candidates, tickets
    candidate = candidates[0]
    assert candidate["status"] == "pending"
    assert "驳回" in candidate["title"] or "经验" in candidate["title"]

    resp = client.post(f"/ops/tickets/{candidate['id']}/approve",
                       json={"reason": "纳入知识库"})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["knowledge"]["title"]

    knowledge = client.get("/ops/knowledge?q=驳回").get_json()["knowledge"]
    assert knowledge
    assert knowledge[0]["ticket_id"] == candidate["id"]

    # 批准后的知识候选也不能再修改。
    assert client.put(f"/ops/tickets/{candidate['id']}",
                      json={"title": "x", "reason": "x"}).status_code == 403


def test_assistant_without_ai_returns_error(env):
    client, worker = env["client"], env["worker"]
    _login(client, "alice")
    resp = client.post("/ops/assistant/suggest", json={
        "node_id": NODE_ID,
        "request": "磁盘满了怎么排查？",
    })
    assert resp.status_code == 502
    assert "AI" in resp.get_json()["error"]
    assert worker.submitted == []


def test_fts_search_finds_chinese_ticket(env):
    client = env["client"]
    _login(client, "alice")
    _create_ticket(client, title="磁盘空间排查", request="根分区写满时的排查步骤")
    data = client.get("/ops/tickets?q=磁盘空间").get_json()
    assert data["tickets"]
    assert data["tickets"][0]["title"] == "磁盘空间排查"


def test_ai_suggestion_is_not_executable_without_admin(env):
    client = env["client"]
    _login(client, "alice")
    # 普通用户即使手工创建带 AI 建议字段的工单，也不能执行。
    resp = client.post("/ops/tickets", json={
        "node_id": NODE_ID,
        "request": "AI 建议执行",
        "command": "echo should-not-run",
        "ai_suggestion": {"command": "echo should-not-run"},
        "execute": True,
    })
    assert resp.status_code == 201
    assert env["worker"].submitted == []


def test_user_knowledge_candidate_cannot_spoof_approval(env):
    client = env["client"]
    _login(client, "alice")
    resp = client.post("/ops/tickets", json={
        "kind": "knowledge",
        "status": "approved",
        "created_by": "root",
        "knowledge_payload": {
            "title": "伪造知识", "content": "不应直接入库",
        },
    })
    assert resp.status_code == 201, resp.get_json()
    ticket = resp.get_json()["ticket"]
    assert ticket["status"] == "pending"
    assert ticket["created_by"] == "alice"
    # 知识库仍为空，必须管理员批准才会入库。
    assert client.get("/ops/knowledge").get_json()["knowledge"] == []


def test_command_requires_root_safe_exec_env(env):
    client = env["client"]
    _login(client, "alice")
    resp = client.post("/ops/tickets", json={
        "node_id": NODE_ID,
        "request": "尝试切换用户",
        "command": "id",
        "exec_env": {"user": "alice"},
    })
    assert resp.status_code == 400
    assert "root" in resp.get_json()["error"]


def test_duplicate_executor_start_does_not_overwrite_success(env):
    client, worker = env["client"], env["worker"]
    _login(client, "alice")
    ticket_id = _create_ticket(client).get_json()["ticket"]["id"]
    _login(client, "root")
    assert client.post(f"/ops/tickets/{ticket_id}/approve",
                       json={"reason": "允许"}).status_code == 200

    from neuops.master.services.rroot.executor import start_execution
    # 同步模式下连续启动两次：第二次必须看到状态已变化并安全退出。
    start_execution(ticket_id, "root")
    start_execution(ticket_id, "root")
    data = client.get(f"/ops/tickets/{ticket_id}").get_json()
    assert data["status"] == "succeeded"
    assert len(worker.submitted) == 1


def test_assistant_parses_ai_json_into_command_suggestion(env, monkeypatch):
    monkeypatch.setenv("NEUOPS_AI_ENABLED", "1")
    monkeypatch.setenv("NEUOPS_AI_API_KEY", "test-key")
    monkeypatch.setenv("NEUOPS_AI_MODEL", "test-model")

    from neuops.master.services.rroot.ai import AIClient

    def fake_complete_json(self, system_prompt, user_prompt, **kwargs):
        return json.dumps({
            "reply": "建议先只读排查",
            "needs_clarification": False,
            "command": "df -h /var",
            "explanation": "只读命令，风险低",
            "risk_level": "low",
            "risk_notes": ["不要直接删除文件"],
            "exec_env": {"workdir": "/root", "timeout_seconds": 120},
            "alternatives": ["du -sh /var/*"],
            "source_ticket_ids": [],
        })

    monkeypatch.setattr(AIClient, "complete_json", fake_complete_json)
    client, worker = env["client"], env["worker"]
    _login(client, "alice")
    resp = client.post("/ops/assistant/suggest", json={
        "node_id": NODE_ID,
        "request": "/var 满了怎么排查？",
    })
    assert resp.status_code == 200, resp.get_json()
    payload = resp.get_json()
    assert "ticket" not in payload
    assert payload["suggestion"]["command"] == "df -h /var"
    assert payload["suggestion"]["model"] == "test-model"
    assert payload["suggestion"]["exec_env"]["timeout_seconds"] == 120
    assert worker.submitted == []

    # 用户确认后，建议随普通工单一并提交，仍进入 pending 审批。
    created = client.post("/ops/tickets", json={
        "node_id": NODE_ID,
        "request": "/var 满了怎么排查？",
        "command": payload["suggestion"]["command"],
        "ai_suggestion": payload["suggestion"],
        "ai_model": payload["suggestion"]["model"],
    })
    assert created.status_code == 201, created.get_json()
    ticket = created.get_json()["ticket"]
    assert ticket["status"] == "pending"
    assert ticket["ai_suggestion"]["command"] == "df -h /var"
    assert worker.submitted == []


def test_assistant_rejects_empty_command_after_retries(env, monkeypatch):
    monkeypatch.setenv("NEUOPS_AI_ENABLED", "1")
    monkeypatch.setenv("NEUOPS_AI_API_KEY", "test-key")
    monkeypatch.setenv("NEUOPS_AI_MODEL", "test-model")

    from neuops.master.services.rroot.ai import AIClient

    calls = []

    def fake_complete_json(self, system_prompt, user_prompt, **kwargs):
        calls.append(user_prompt)
        return json.dumps({
            "reply": "还需要确认一些信息",
            "needs_clarification": True,
            "command": "",
            "explanation": "",
            "risk_level": "unknown",
            "risk_notes": [],
            "exec_env": {},
            "alternatives": [],
            "source_ticket_ids": [],
        })

    monkeypatch.setattr(AIClient, "complete_json", fake_complete_json)
    client = env["client"]
    _login(client, "alice")
    resp = client.post("/ops/assistant/suggest", json={
        "node_id": NODE_ID,
        "request": "ascend 新建用户 pengyt",
    })
    assert resp.status_code == 502
    assert "未生成可执行命令" in resp.get_json()["error"]
    # 初始请求 + 2 次严格重试
    assert len(calls) == 3
    assert "command 不得为空" in calls[1]


def test_assistant_retries_until_command_is_returned(env, monkeypatch):
    monkeypatch.setenv("NEUOPS_AI_ENABLED", "1")
    monkeypatch.setenv("NEUOPS_AI_API_KEY", "test-key")
    monkeypatch.setenv("NEUOPS_AI_MODEL", "test-model")

    from neuops.master.services.rroot.ai import AIClient

    calls = []

    def fake_complete_json(self, system_prompt, user_prompt, **kwargs):
        calls.append(user_prompt)
        command = "" if len(calls) == 1 else "useradd -m -s /bin/bash pengyt"
        return json.dumps({
            "reply": "按系统默认方式创建用户",
            "needs_clarification": True,
            "command": command,
            "explanation": "最小用户创建命令；未加入额外设备组",
            "risk_level": "low",
            "risk_notes": ["需确认 Ascend 设备组要求"],
            "exec_env": {"workdir": "/root", "timeout_seconds": 120},
            "alternatives": [],
            "source_ticket_ids": [],
        })

    monkeypatch.setattr(AIClient, "complete_json", fake_complete_json)
    client = env["client"]
    _login(client, "alice")
    resp = client.post("/ops/assistant/suggest", json={
        "node_id": NODE_ID,
        "request": "ascend 新建用户 pengyt",
    })
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["suggestion"]["command"] == (
        "useradd -m -s /bin/bash pengyt")
    assert len(calls) == 2


def test_new_command_ticket_notifies_admin_email(env, monkeypatch):
    client = env["client"]
    db = env["db"]
    admin = db.get_user_by_username("root")
    assert db.update_email(admin["id"], "admin@example.com")

    monkeypatch.setenv("NEUOPS_MAIL_SYNC", "1")
    monkeypatch.setenv("NEUOPS_SMTP_USER", "bot@qq.com")
    monkeypatch.setenv("NEUOPS_SMTP_PASSWORD", "qq-auth-code")
    captured = {}

    from neuops.master.services.mailer import Mailer

    def fake_send(self, to, subject, body):
        captured.update(to=to, subject=subject, body=body)

    monkeypatch.setattr(Mailer, "send", fake_send)
    _login(client, "alice")
    resp = _create_ticket(client, command="df -h /var")
    assert resp.status_code == 201, resp.get_json()

    assert captured["to"] == ["admin@example.com"]
    assert "新命令工单待审批" in captured["subject"]
    assert "df -h /var" in captured["body"]
    assert resp.get_json()["ticket"]["id"] in captured["body"]


def test_knowledge_ticket_does_not_send_command_mail(env, monkeypatch):
    client, db = env["client"], env["db"]
    admin = db.get_user_by_username("root")
    db.update_email(admin["id"], "admin@example.com")
    monkeypatch.setenv("NEUOPS_MAIL_SYNC", "1")
    monkeypatch.setenv("NEUOPS_SMTP_USER", "bot@qq.com")
    monkeypatch.setenv("NEUOPS_SMTP_PASSWORD", "qq-auth-code")
    sent = []
    from neuops.master.services.mailer import Mailer
    monkeypatch.setattr(Mailer, "send",
                        lambda self, to, subject, body: sent.append(subject))
    _login(client, "alice")
    resp = client.post("/ops/tickets", json={
        "kind": "knowledge",
        "title": "经验",
        "knowledge_payload": {"title": "经验", "content": "内容"},
    })
    assert resp.status_code == 201
    assert sent == []


def test_admin_can_edit_and_delete_knowledge_without_approval(env):
    client = env["client"]
    _login(client, "root")
    created = client.post("/ops/tickets", json={
        "kind": "knowledge",
        "knowledge_payload": {
            "title": "旧知识标题", "content": "旧内容",
            "kind": "experience", "tags": ["old"],
        },
    })
    assert created.status_code == 201, created.get_json()
    ticket_id = created.get_json()["ticket"]["id"]
    approved = client.post(f"/ops/tickets/{ticket_id}/approve",
                           json={"reason": "入库"})
    assert approved.status_code == 200, approved.get_json()
    knowledge_id = approved.get_json()["knowledge"]["id"]

    updated = client.put(f"/ops/knowledge/{knowledge_id}", json={
        "title": "新知识标题", "content": "新内容",
        "kind": "runbook", "tags": ["new"],
    })
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["title"] == "新知识标题"
    assert updated.get_json()["kind"] == "runbook"
    found = client.get("/ops/knowledge?q=新知识标题").get_json()["knowledge"]
    assert found and found[0]["id"] == knowledge_id

    # 普通用户看不到编辑/删除入口，服务端也拒绝。
    _login(client, "alice")
    assert client.put(f"/ops/knowledge/{knowledge_id}",
                      json={"title": "x"}).status_code == 403
    assert client.delete(f"/ops/knowledge/{knowledge_id}").status_code == 403

    _login(client, "root")
    assert client.delete(f"/ops/knowledge/{knowledge_id}").status_code == 200
    assert client.get("/ops/knowledge?q=新知识标题").get_json()["knowledge"] == []


def test_agent_calls_readonly_tool_and_streams_progress(env, monkeypatch):
    monkeypatch.setenv("NEUOPS_AI_ENABLED", "1")
    monkeypatch.setenv("NEUOPS_AI_API_KEY", "test-key")
    monkeypatch.setenv("NEUOPS_AI_MODEL", "test-model")

    from neuops.master.services.rroot.ai import AIClient
    import neuops.master.services.rroot.ai as ai_module

    calls = []

    def fake_complete_json(self, system_prompt, user_prompt, **kwargs):
        calls.append(user_prompt)
        if len(calls) == 1:
            return json.dumps({
                "type": "tool_call", "name": "ls",
                "arguments": {"path": "/var/log"},
            })
        return json.dumps({
            "type": "final",
            "suggestion": {
                "reply": "查看日志目录后给出命令",
                "needs_clarification": False,
                "command": "du -sh /var/log",
                "explanation": "只读排查",
                "risk_level": "low",
                "risk_notes": [],
                "exec_env": {"workdir": "/root", "timeout_seconds": 120},
                "alternatives": [],
                "source_ticket_ids": [],
            },
        })

    tool_calls = []

    def fake_execute_tool(node_id, user_id, tool, args, cwd="/"):
        tool_calls.append((node_id, user_id, tool, args, cwd))
        return {
            "tool": tool, "args": args, "cwd": cwd,
            "output": "total 0\ndrwxr-xr-x 2 root root 6 var",
            "exit_code": 0, "timed_out": False, "duration": 0.01,
        }

    monkeypatch.setattr(AIClient, "complete_json", fake_complete_json)
    monkeypatch.setattr(ai_module, "execute_tool", fake_execute_tool)
    client = env["client"]
    _login(client, "alice")
    resp = client.post("/ops/assistant/agent", json={
        "node_id": NODE_ID,
        "request": "查看日志目录占用",
    })
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    events = [
        json.loads(line[len("data: "):])
        for line in resp.get_data(as_text=True).splitlines()
        if line.startswith("data: ")
    ]
    assert any(e.get("type") == "tool_start" for e in events)
    assert any(e.get("type") == "tool_result" for e in events)
    result = [e for e in events if e.get("type") == "result"][0]
    assert result["result"]["suggestion"]["command"] == "du -sh /var/log"
    assert tool_calls
    assert tool_calls[0][2] == "ls"
    assert tool_calls[0][3] == {"path": "/var/log"}
    assert tool_calls[0][1] == "alice"


def test_successful_command_does_not_auto_create_knowledge(env):
    client = env["client"]
    _login(client, "alice")
    ticket_id = _create_ticket(client, command="df -h /var").get_json()[
        "ticket"]["id"]
    _login(client, "root")
    assert client.post(f"/ops/tickets/{ticket_id}/approve", json={
        "reason": "允许执行", "execute": True,
    }).status_code == 200
    assert client.get(f"/ops/tickets/{ticket_id}").get_json()["status"] == "succeeded"

    candidates = client.get("/ops/tickets?kind=knowledge").get_json()["tickets"]
    assert [c for c in candidates if c["source_ticket_id"] == ticket_id] == []
