"""NeuOps 运维工单 HTTP API（Rroot）。

所有接口都需要登录；批准/驳回/执行/批量学习仅管理员。普通用户与 AI
只能创建/查看/修改草稿，不能触发 root 执行。
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from neuops.master.api.auth import get_current_user, login_required
from neuops.master.api.permissions import require_admin
from neuops.master.services.rroot import service
from neuops.master.services.rroot.ai import AIError
from neuops.master.services.rroot.executor import ExecutionError, read_exec_log
from neuops.master.services.rroot.security import ValidationError, public_ticket

ops_bp = Blueprint("ops", __name__)
logger = logging.getLogger("master.rroot.api")


def _json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _respond_error(exc: Exception):
    if isinstance(exc, LookupError):
        return {"error": str(exc)}, 404
    if isinstance(exc, PermissionError):
        return {"error": str(exc)}, 403
    if isinstance(exc, ValidationError):
        return {"error": str(exc)}, 400
    if isinstance(exc, ExecutionError):
        return {"error": str(exc)}, 409
    if isinstance(exc, AIError):
        return {"error": str(exc)}, 502
    logger.exception("Rroot API 未处理异常")
    return {"error": "服务器内部错误"}, 500


@ops_bp.get("/stats")
@login_required
def stats():
    return service.stats(), 200


@ops_bp.get("/tickets")
@login_required
def list_tickets():
    user = get_current_user()
    try:
        return {
            "tickets": service.list_tickets(
                user,
                status=(request.args.get("status") or "").strip(),
                kind=(request.args.get("kind") or "").strip(),
                search=(request.args.get("q") or "").strip(),
                limit=request.args.get("limit", 100, type=int),
            ),
        }, 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.post("/tickets")
@login_required
def create_ticket():
    user = get_current_user()
    try:
        ticket = service.create_ticket(user, _json_body())
        return {"ticket": service.get_ticket(user, ticket["id"])}, 201
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.get("/tickets/<ticket_id>")
@login_required
def get_ticket(ticket_id: str):
    user = get_current_user()
    try:
        return service.get_ticket(user, ticket_id), 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.put("/tickets/<ticket_id>")
@login_required
def update_ticket(ticket_id: str):
    user = get_current_user()
    try:
        return service.update_ticket(user, ticket_id, _json_body()), 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.post("/tickets/<ticket_id>/submit")
@login_required
def submit_ticket(ticket_id: str):
    user = get_current_user()
    try:
        return service.submit_ticket(user, ticket_id), 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.post("/tickets/<ticket_id>/cancel")
@login_required
def cancel_ticket(ticket_id: str):
    user = get_current_user()
    try:
        return service.cancel_ticket(user, ticket_id), 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.post("/tickets/<ticket_id>/approve")
@require_admin
def approve_ticket(ticket_id: str):
    user = get_current_user()
    try:
        return service.approve_ticket(user, ticket_id, _json_body()), 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.post("/tickets/<ticket_id>/reject")
@require_admin
def reject_ticket(ticket_id: str):
    user = get_current_user()
    try:
        return service.reject_ticket(user, ticket_id, _json_body()), 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.post("/tickets/<ticket_id>/execute")
@require_admin
def execute_ticket(ticket_id: str):
    user = get_current_user()
    try:
        return service.execute_ticket(user, ticket_id), 202
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.get("/tickets/<ticket_id>/log")
@login_required
def ticket_log(ticket_id: str):
    user = get_current_user()
    try:
        ticket = service.get_ticket(user, ticket_id, include_events=False)
        raw = request.args.get("raw") in {"1", "true", "yes"}
        text = read_exec_log(ticket)
        if raw:
            return text, 200, {"Content-Type": "text/plain; charset=utf-8"}
        return {
            "log": text,
            "exec_result": ticket.get("exec_result") or {},
            "execution_status": ticket.get("execution_status"),
            "worker_task_id": ticket.get("worker_task_id"),
        }, 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.post("/assistant/suggest")
@login_required
def assistant_suggest():
    """仅生成命令建议；不创建工单，更不能执行。"""
    user = get_current_user()
    try:
        return service.assistant_suggest(user, _json_body()), 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.get("/knowledge")
@login_required
def list_knowledge():
    try:
        return {
            "knowledge": service.list_knowledge(
                search=(request.args.get("q") or "").strip(),
                kind=(request.args.get("kind") or "").strip(),
                limit=request.args.get("limit", 100, type=int),
            ),
        }, 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.get("/knowledge/<knowledge_id>")
@login_required
def get_knowledge(knowledge_id: str):
    try:
        return service.get_knowledge(knowledge_id), 200
    except Exception as exc:
        return _respond_error(exc)


@ops_bp.post("/knowledge/learn")
@require_admin
def learn_knowledge():
    """管理员手动触发学习；不传 ticket_id 时扫描最近终态工单。"""
    data = _json_body()
    try:
        created = []
        ticket_id = str(data.get("ticket_id") or "").strip()
        if ticket_id:
            created = service.learn_from_ticket(ticket_id, force=True)
        else:
            tickets = service.list_tickets(
                get_current_user(), limit=10,
            )
            for ticket in tickets:
                if ticket.get("status") in {"rejected", "succeeded", "failed"}:
                    created.extend(
                        service.learn_from_ticket(ticket["id"], force=False))
        return {
            "created": [public_ticket(ticket) for ticket in created],
            "count": len(created),
        }, 200
    except Exception as exc:
        return _respond_error(exc)
