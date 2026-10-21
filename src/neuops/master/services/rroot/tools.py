"""AI Agent 的只读工具执行器。

AI 只能输出结构化的工具调用；命令由本模块根据工具名和参数重新构造，
不执行 AI 返回的原始 Shell。所有工具都只读，并经过参数类型/长度校验。

工具通过 worker 的普通 /tasks 队列执行，默认使用当前用户在节点上的
凭据身份（与普通任务一致），而不是默认 root。可用
``NEUOPS_AGENT_TOOL_USER`` 显式指定只读工具账号。
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
import shlex
import time
from typing import Any

from neuops.config import env_int, env_text
from neuops.master.services.nodes_pool import Nodes_Pool

from .security import ValidationError, redact_secrets


logger = logging.getLogger("master.rroot.tools")

MAX_OUTPUT_CHARS = 12_000
DEFAULT_TIMEOUT = max(5, env_int("NEUOPS_AGENT_TOOL_TIMEOUT", 60))
MAX_PATH = 4096
MAX_PATTERN = 500
MAX_LINES = 500
MAX_STEPS = max(1, env_int("NEUOPS_AGENT_MAX_STEPS", 5))
TOOL_PRIORITY = max(0, min(1, env_int("NEUOPS_AGENT_TOOL_PRIORITY", 1)))

TOOL_NAMES = frozenset({
    "pwd", "cd", "ls", "cat", "head", "tail", "stat", "du", "df",
    "ps", "grep", "find",
})

TOOL_DESCRIPTIONS = """
可用只读工具：
- pwd(): 当前工作目录
- cd(path): 切换工作目录（只影响后续工具调用，不执行命令）
- ls(path="."): 列出目录内容（ls -la）
- cat(path): 读取文件开头，最多 20000 字节
- head(path, lines=80): 读取文件开头若干行
- tail(path, lines=80): 读取文件末尾若干行
- stat(path): 文件/目录元信息
- du(path="."): 目录占用（du -sh）
- df(): 文件系统使用情况（df -h）
- ps(): 进程列表（ps aux）
- grep(pattern, path="."): 子串/正则搜索，最多 100 行
- find(path=".", name="*", maxdepth=2): 查找文件，最多 200 行

工具调用格式（每轮只输出一个 JSON）：
{"type":"tool","tool":"ls","args":{"path":"/var/log"}}

最终建议格式：
{"type":"final","suggestion":{...与最终命令 schema 相同...}}
"""

_SENSITIVE_PATH_RE = re.compile(
    r"(?i)(/etc/(shadow|gshadow)|/proc/[^/]+/environ|"
    r"(^|/)\.ssh/|id_rsa|id_ed25519|\.pem$|\.key$)"
)


class ToolError(RuntimeError):
    """工具调用参数或执行失败。"""


def _clean_path(value: Any, *, default: str = ".") -> str:
    path = str(value if value is not None else default).strip()
    if not path:
        path = default
    if len(path) > MAX_PATH or "\x00" in path or "\n" in path:
        raise ValidationError("路径参数非法")
    if _SENSITIVE_PATH_RE.search(path):
        raise ValidationError("该路径不允许通过 AI 只读工具访问")
    return path


def _clean_pattern(value: Any) -> str:
    pattern = str(value or "").strip()
    if not pattern or len(pattern) > MAX_PATTERN or "\x00" in pattern:
        raise ValidationError("搜索模式非法")
    return pattern


def _clean_lines(value: Any, default: int = 80) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("lines 必须是整数")
    return max(1, min(int(value), MAX_LINES))


def _clean_depth(value: Any, default: int = 2) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("maxdepth 必须是整数")
    return max(1, min(int(value), 5))


def _resolve_cwd(cwd: str, path: str) -> str:
    if path.startswith("/"):
        resolved = posixpath.normpath(path)
    else:
        resolved = posixpath.normpath(posixpath.join(cwd or "/", path))
    if not resolved.startswith("/"):
        resolved = "/" + resolved
    if _SENSITIVE_PATH_RE.search(resolved):
        raise ValidationError("该路径不允许通过 AI 只读工具访问")
    return resolved


def _shell(command: str, cwd: str) -> str:
    return f"cd {shlex.quote(cwd or '/')} && {command}"


def build_tool_command(tool: str, args: dict, cwd: str) -> tuple[str, str]:
    """返回 (shell 命令, 新 cwd)。命令由白名单工具和参数重建。"""
    tool = str(tool or "").strip()
    if tool not in TOOL_NAMES:
        raise ValidationError(f"不支持的工具: {tool}")
    args = args if isinstance(args, dict) else {}

    if tool == "pwd":
        return _shell("pwd", cwd), cwd
    if tool == "cd":
        new_cwd = _resolve_cwd(cwd, _clean_path(args.get("path"), default="/"))
        return "", new_cwd
    if tool == "ls":
        path = shlex.quote(_clean_path(args.get("path"), default="."))
        return _shell(f"ls -la -- {path}", cwd), cwd
    if tool == "cat":
        path = shlex.quote(_clean_path(args.get("path")))
        return _shell(f"head -c 20000 -- {path}", cwd), cwd
    if tool == "head":
        path = shlex.quote(_clean_path(args.get("path")))
        lines = _clean_lines(args.get("lines"))
        return _shell(f"head -n {lines} -- {path}", cwd), cwd
    if tool == "tail":
        path = shlex.quote(_clean_path(args.get("path")))
        lines = _clean_lines(args.get("lines"))
        return _shell(f"tail -n {lines} -- {path}", cwd), cwd
    if tool == "stat":
        path = shlex.quote(_clean_path(args.get("path")))
        return _shell(f"stat -- {path}", cwd), cwd
    if tool == "du":
        path = shlex.quote(_clean_path(args.get("path"), default="."))
        return _shell(f"du -sh -- {path} 2>/dev/null", cwd), cwd
    if tool == "df":
        return _shell("df -h", cwd), cwd
    if tool == "ps":
        return _shell("ps aux", cwd), cwd
    if tool == "grep":
        pattern = shlex.quote(_clean_pattern(args.get("pattern")))
        path = shlex.quote(_clean_path(args.get("path"), default="."))
        return _shell(
            f"grep -n -e {pattern} -- {path} 2>/dev/null | head -n 100", cwd
        ), cwd
    if tool == "find":
        path = shlex.quote(_clean_path(args.get("path"), default="."))
        name = shlex.quote(str(args.get("name") or "*")[:200])
        depth = _clean_depth(args.get("maxdepth"))
        return _shell(
            f"find {path} -maxdepth {depth} -name {name} "
            "2>/dev/null | head -n 200", cwd
        ), cwd
    raise ValidationError(f"不支持的工具: {tool}")


def _worker_error(resp) -> str:
    try:
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])
    except Exception:
        pass
    return (getattr(resp, "text", "") or "").strip()[:500]


def _fetch_log(pool: Nodes_Pool, node_id: str, task_id: str) -> str:
    try:
        resp = pool.forward_get_to_node(
            node_id, f"/tasks/{task_id}/log", params={"raw": "1"}, timeout=15,
        )
        return resp.text if resp.ok else f"[日志读取失败] {_worker_error(resp)}"
    except ValueError as exc:
        return f"[日志读取失败] {exc}"


def execute_tool(node_id: str, user_id: str, tool: str, args: dict,
                 cwd: str = "/") -> dict:
    """执行一个只读工具并返回结构化结果。"""
    started = time.monotonic()
    command, new_cwd = build_tool_command(tool, args, cwd)
    result = {
        "tool": tool,
        "args": args or {},
        "cwd": new_cwd,
        "command": command,
        "output": "",
        "exit_code": None,
        "duration": 0.0,
        "timed_out": False,
    }
    # cd 只改变 agent 的虚拟 cwd，不需要访问 worker。
    if not command:
        result["output"] = new_cwd
        result["exit_code"] = 0
        result["duration"] = round(time.monotonic() - started, 3)
        return result

    pool = Nodes_Pool.get_nodes_pool()
    node = pool.get_node_by_id(node_id)
    if not node:
        raise ToolError("目标节点不存在")
    body = {
        "command": command,
        "user_id": user_id,
        "cpu": 0,
        "memory": 0,
        "mem_unit": "GB",
        "device_num": 0,
        "priority": TOOL_PRIORITY,
        "est_time": 0,
        "target": {"type": "host"},
    }
    try:
        resp = pool.forward_to_node(node_id, "/tasks", body, timeout=20)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if not resp.ok:
        raise ToolError(f"worker 拒绝工具任务: {_worker_error(resp)}")
    try:
        payload = resp.json()
        task_id = str(payload.get("task_id") or "")
    except Exception:
        task_id = ""
    if not task_id:
        raise ToolError("worker 未返回 task_id")

    deadline = time.monotonic() + DEFAULT_TIMEOUT
    task = {}
    while time.monotonic() < deadline:
        try:
            status_resp = pool.forward_get_to_node(
                node_id, f"/tasks/{task_id}", timeout=10,
            )
            if status_resp.ok:
                task = status_resp.json() or {}
                if task.get("status") in {"completed", "failed", "deleted"}:
                    break
        except (ValueError, Exception):
            pass
        time.sleep(0.5)
    else:
        result["timed_out"] = True

    output = _fetch_log(pool, node_id, task_id)
    output = redact_secrets(output, limit=MAX_OUTPUT_CHARS)
    worker_result = (task or {}).get("result") or {}
    result.update({
        "output": output,
        "exit_code": worker_result.get("returncode"),
        "timed_out": bool(result["timed_out"] or worker_result.get("timed_out")),
        "duration": round(time.monotonic() - started, 3),
    })
    return result


def tool_user_for(user: dict | None, node: dict | None) -> str:
    """只读工具执行身份：环境覆盖 > 节点凭据 > WebUI 用户名。"""
    override = env_text("NEUOPS_AGENT_TOOL_USER")
    if override:
        return override
    user = user or {}
    node = node or {}
    username = str(user.get("username") or "")
    node_name = str(node.get("name") or "")
    if node_name and username:
        try:
            from neuops.master.services.db import Database

            cred = Database.get_instance().get_credential(
                str(user.get("id") or ""), node_name)
            if cred and cred.get("username"):
                tool_user = str(cred["username"])
                if tool_user == "root" and user.get("role") != "admin":
                    return ""
                return tool_user
        except Exception:
            pass
    if username == "root" and user.get("role") != "admin":
        return ""
    return username
