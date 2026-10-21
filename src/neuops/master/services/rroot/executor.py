"""Rroot 特权执行器：唯一允许向 worker 提交 root 命令的代码路径。

与 ``master/api/tasks.py`` 的普通任务转发完全隔离：
  * 不接受任意 user_id，固定 ``root``；
  * 只能执行已 approved 且审批快照校验通过的工单；
  * 通过一次性 token + 原子状态迁移保证单次授权只执行一次；
  * 执行结果（stdout/stderr/退出码/完整日志）回写工单。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from neuops.master.paths import ops_logs_dir
from neuops.master.services.nodes_pool import Nodes_Pool

from .security import (
    ValidationError,
    build_wrapped_command,
    classify_risk,
    json_loads,
    now,
    parse_wrapped_output,
    verify_snapshot,
)
from .store import OpsStore, ops_store


logger = logging.getLogger("master.rroot.executor")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)) or default))
    except ValueError:
        return default


_POLL_INTERVAL = max(0.2, _env_int("NEUOPS_OPS_POLL_INTERVAL", 2, 1) / 1.0)
_EXECUTOR = ThreadPoolExecutor(
    max_workers=_env_int("NEUOPS_OPS_WORKERS", 2),
    thread_name_prefix="rroot-exec",
)
_MAX_DB_OUTPUT = _env_int("NEUOPS_OPS_MAX_OUTPUT", 200000, 1000)


class ExecutionError(RuntimeError):
    """工单无法执行（状态、审批、节点或 worker 错误）。"""


def _execute_sync() -> bool:
    return os.getenv("NEUOPS_OPS_EXECUTE_SYNC", "0").lower() in {
        "1", "true", "yes", "on",
    }


def _node_address(node) -> str:
    return f"{node.ip}:{node.port}"


def _save_log(ticket_id: str, text: str) -> str:
    directory = ops_logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ticket_id}.log"
    temporary = path.with_suffix(".log.tmp")
    temporary.write_text(text or "", encoding="utf-8")
    os.replace(temporary, path)
    return str(path)


def _worker_error(resp) -> str:
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    text = (getattr(resp, "text", "") or "").strip()
    return text[:500] or f"HTTP {getattr(resp, 'status_code', '?')}"


def _fetch_log(pool: Nodes_Pool, node_id: str, task_id: str) -> str:
    try:
        resp = pool.forward_get_to_node(
            node_id, f"/tasks/{task_id}/log",
            params={"raw": "1"}, timeout=30,
        )
        if resp.ok:
            return resp.text or ""
        return f"[读取日志失败] HTTP {resp.status_code}: {_worker_error(resp)}"
    except ValueError as exc:
        return f"[读取日志失败] {exc}"


def _poll_task(pool: Nodes_Pool, node_id: str, task_id: str,
               deadline: float) -> tuple[dict, str]:
    """轮询 worker 任务直到终态；返回 (任务元数据, 错误信息)。"""
    last_error = ""
    while time.monotonic() < deadline:
        try:
            resp = pool.forward_get_to_node(
                node_id, f"/tasks/{task_id}", timeout=15,
            )
            if resp.ok:
                payload = resp.json()
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "")
                    if status in {"completed", "failed"}:
                        return payload, last_error
                    if status == "deleted":
                        return payload, "worker 任务已被删除"
            else:
                last_error = _worker_error(resp)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(max(0.2, _POLL_INTERVAL))
    return {}, last_error or "执行超时"


def _finalize(ticket_id: str, status: str, result: dict, actor: str,
              store: OpsStore) -> None:
    ticket = store.get_ticket(ticket_id) or {}
    store.update_ticket(
        ticket_id,
        status=status,
        execution_status=status,
        exec_result=result,
        exec_log_path=result.get("log_path", ""),
        worker_task_id=result.get("worker_task_id", ""),
    )
    store.append_event(ticket_id, actor, f"execution_{status}", {
        "returncode": result.get("returncode"),
        "timed_out": result.get("timed_out"),
        "worker_task_id": result.get("worker_task_id"),
    })
    try:
        from .service import schedule_learning

        schedule_learning(ticket_id)
    except Exception as exc:  # 学习失败不影响执行结果
        logger.warning("工单 %s 学习调度失败: %s", ticket_id, exc)


def _run(ticket_id: str, actor: str) -> None:
    store = ops_store()
    pool = Nodes_Pool.get_nodes_pool()
    started = time.monotonic()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        return
    claimed = False
    try:
        if ticket.get("kind") == "knowledge":
            raise ExecutionError("知识工单不能执行")
        if ticket.get("status") != "approved":
            raise ExecutionError(f"工单状态为 {ticket.get('status')}，不可执行")
        snapshot = verify_snapshot(ticket)
        node = pool.get_node_by_id(ticket["target_node_id"])
        if not node:
            raise ExecutionError("目标节点不存在或已从节点池移除")
        if ticket.get("target_node_address") and \
                _node_address(node) != ticket["target_node_address"]:
            raise ExecutionError(
                "目标节点地址已变化，审批快照失效，请重新提交审批"
            )
        if not store.claim_for_execution(ticket_id, actor):
            # 另一个执行器已经拿到一次性授权；不要把它覆盖成 failed。
            logger.info("工单 %s 已被其他执行器 claim，跳过", ticket_id)
            return
        claimed = True
        store.append_event(ticket_id, actor, "execution_started", {
            "node_id": ticket["target_node_id"],
            "node_name": ticket["target_node_name"],
            "worker_task_id": None,
        })

        params = json_loads(ticket.get("params"), {})
        exec_env = snapshot.get("exec_env") or json_loads(
            ticket.get("exec_env"), {})
        timeout = max(1, int(exec_env.get("timeout_seconds") or 3600))
        marker = f"__NEUOPS_{secrets.token_hex(12)}__"
        wrapped = build_wrapped_command(
            ticket["command"], marker, timeout,
            workdir=str(exec_env.get("workdir") or "/root"),
            env=exec_env.get("env") or {},
        )
        body = {
            "command": wrapped,
            "user_id": "root",
            "cpu": int(params.get("cpu") or 0),
            "memory": int(params.get("memory") or 0),
            "mem_unit": params.get("mem_unit") or "GB",
            "device_num": int(params.get("device_num") or 0),
            "priority": int(params.get("priority") or 0),
            "est_time": int(params.get("est_time") or 0),
            "target": {"type": "host"},
        }
        try:
            resp = pool.forward_to_node(
                ticket["target_node_id"], "/tasks", body, timeout=30,
            )
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        if not resp.ok:
            raise ExecutionError(f"worker 拒绝了任务: {_worker_error(resp)}")
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict) or not payload.get("task_id"):
            raise ExecutionError("worker 未返回 task_id")
        worker_task_id = str(payload["task_id"])
        store.update_ticket(ticket_id, worker_task_id=worker_task_id)
        store.append_event(ticket_id, actor, "worker_task_submitted", {
            "worker_task_id": worker_task_id,
            "node_id": ticket["target_node_id"],
        })

        deadline = time.monotonic() + max(300, timeout + 180)
        task_payload, poll_error = _poll_task(
            pool, ticket["target_node_id"], worker_task_id, deadline,
        )
        worker_result = (task_payload or {}).get("result") or {}
        worker_status = str((task_payload or {}).get("status") or "")
        log_text = _fetch_log(pool, ticket["target_node_id"], worker_task_id)
        log_path = _save_log(ticket_id, log_text)
        parsed = parse_wrapped_output(log_text, marker)
        returncode = worker_result.get("returncode")
        timed_out = bool(worker_result.get("timed_out"))
        if returncode is None:
            returncode = 124 if timed_out else -1
        if int(returncode) == 124:
            timed_out = True
        succeeded = (
            worker_status == "completed"
            and int(returncode) == 0
            and not timed_out
        )
        result = {
            "worker_task_id": worker_task_id,
            "worker_status": worker_status,
            "returncode": int(returncode),
            "timed_out": timed_out,
            "error": worker_result.get("error") or poll_error or None,
            "stdout": parsed["stdout"][:_MAX_DB_OUTPUT],
            "stderr": parsed["stderr"][:_MAX_DB_OUTPUT],
            "markers_found": parsed["markers_found"],
            "log_path": log_path,
            "log_bytes": len(log_text.encode("utf-8", "replace")),
            "duration_seconds": round(time.monotonic() - started, 3),
            "node_id": ticket["target_node_id"],
            "node_name": ticket["target_node_name"],
            "command_risk": classify_risk(ticket["command"]),
        }
        _finalize(
            ticket_id, "succeeded" if succeeded else "failed",
            result, actor, store,
        )
    except Exception as exc:
        logger.warning("Rroot 工单 %s 执行失败: %s", ticket_id, exc)
        current = store.get_ticket(ticket_id) or {}
        if not claimed and current.get("status") != "approved":
            # 另一个执行器已经推进了状态；不覆盖结果。
            return
        result = {
            "worker_task_id": "",
            "worker_status": "error",
            "returncode": -1,
            "timed_out": False,
            "error": str(exc),
            "stdout": "",
            "stderr": "",
            "markers_found": False,
            "log_path": "",
            "duration_seconds": round(time.monotonic() - started, 3),
            "node_id": ticket.get("target_node_id", ""),
            "node_name": ticket.get("target_node_name", ""),
        }
        try:
            _finalize(ticket_id, "failed", result, actor, store)
        except Exception:
            logger.exception("Rroot 工单 %s 失败状态回写异常", ticket_id)


def start_execution(ticket_id: str, actor: str) -> None:
    """启动一次后台执行；同一工单的重复调用会被原子 claim 拒绝。"""
    if _execute_sync():
        _run(ticket_id, actor)
        return
    _EXECUTOR.submit(_run, ticket_id, actor)


def read_exec_log(ticket: dict) -> str:
    path = str(ticket.get("exec_log_path") or "")
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
