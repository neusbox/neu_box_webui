"""Rroot 业务编排：工单生命周期、管理员审批、AI 建议与自学习。

权限模型：
  * 所有登录用户可创建工单/咨询、查看自己的工单；
  * 管理员可查看全部、修改建议、批准/驳回、触发唯一一次执行；
  * AI 只能写 draft/知识候选，不能批准、不能执行；
  * 只有本模块的 ``approve_ticket(execute=True)`` 会调用执行器。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading

from neuops.master.services.mailer import notify_admins_new_command_ticket
from neuops.master.services.nodes_pool import Nodes_Pool

from .ai import ai_configured, ai_model_name, extract_knowledge, suggest_command
from .executor import start_execution
from .tools import tool_user_for
from .security import (
    KNOWLEDGE_KINDS,
    MAX_REQUEST_CHARS,
    ROOT_USER,
    ValidationError,
    classify_risk,
    clean_text,
    content_hash,
    json_loads,
    now,
    normalize_exec_env,
    normalize_params,
    public_ticket,
    validate_command,
)
from .store import OpsStore, ops_store


logger = logging.getLogger("master.rroot.service")

def _learn_sync() -> bool:
    return os.getenv("NEUOPS_OPS_LEARN_SYNC", "0").lower() in {
        "1", "true", "yes", "on",
    }


def _auto_learn() -> bool:
    return os.getenv("NEUOPS_OPS_AUTO_LEARN", "1").lower() in {
        "1", "true", "yes", "on",
    }


def _notify_new_command(ticket: dict) -> None:
    try:
        notify_admins_new_command_ticket(ticket)
    except Exception:
        logger.exception("命令工单邮件通知失败: %s", ticket.get("id"))


def _is_admin(user: dict | None) -> bool:
    return bool(user and user.get("role") == "admin")


def _username(user: dict | None) -> str:
    return str((user or {}).get("username") or "")


def _user_id(user: dict | None) -> str:
    return str((user or {}).get("id") or "")


def _can_view(user: dict, ticket: dict) -> bool:
    return _is_admin(user) or ticket.get("created_by") == _username(user)


def _can_modify(user: dict, ticket: dict) -> bool:
    if ticket.get("status") not in {"draft", "pending"}:
        return False
    return _is_admin(user) or ticket.get("created_by") == _username(user)


def _node_info(node) -> dict:
    if node is None:
        return {}
    return {
        "node_id": node.node_id,
        "name": node.name,
        "ip": node.ip,
        "port": node.port,
        "address": f"{node.ip}:{node.port}",
        "status": node.status,
        "total_cpu": node.total_cpu,
        "idle_cpu": node.idle_cpu,
        "total_mem": node.total_mem,
        "idle_mem": node.idle_mem,
        "total_devices": node.total_devices,
        "idle_devices": node.idle_devices,
        "active_sandboxes": node.active_sandboxes,
    }


def _require_node(node_id: str) -> dict:
    node_id = str(node_id or "").strip()
    if not node_id:
        raise ValidationError("请选择目标节点")
    node = Nodes_Pool.get_nodes_pool().get_node_by_id(node_id)
    if not node:
        raise ValidationError(f"节点 {node_id} 不存在")
    return _node_info(node)


def _default_title(*values: str) -> str:
    for value in values:
        value = (value or "").strip().replace("\n", " ")
        if value:
            return value[:120]
    return "运维工单"


def _knowledge_payload(data: dict) -> dict:
    payload = data.get("knowledge_payload") or data
    if not isinstance(payload, dict):
        raise ValidationError("知识内容必须是对象")
    title = clean_text(payload.get("title"), field="知识标题",
                       max_bytes=500, allow_empty=False)
    content = clean_text(payload.get("content"), field="知识内容",
                         max_bytes=20_000, allow_empty=False)
    kind = str(payload.get("kind") or "experience")
    if kind not in KNOWLEDGE_KINDS:
        kind = "experience"
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    tags = [str(tag).strip()[:64] for tag in tags if str(tag).strip()][:12]
    source_ids = payload.get("source_ticket_ids") or []
    if not isinstance(source_ids, list):
        source_ids = [source_ids]
    source_ids = [str(item)[:64] for item in source_ids if str(item).strip()][:20]
    return {
        "title": title,
        "content": content,
        "kind": kind,
        "tags": tags,
        "source_ticket_ids": source_ids,
        "content_hash": content_hash(title.lower(), content.lower()),
        "confidence": str(payload.get("confidence") or "")[:32],
    }


def create_ticket(user: dict, data: dict, *, default_status: str = "pending") -> dict:
    """创建命令/咨询/知识工单。命令不会在此处执行。"""
    if not user:
        raise ValidationError("请先登录")
    kind = str(data.get("kind") or "command").strip()
    if kind not in {"command", "consult", "knowledge"}:
        raise ValidationError(f"未知工单类型: {kind}")

    if kind == "knowledge":
        payload = _knowledge_payload(data)
        # 用户提交的知识候选必须先 pending；批准只能走管理员接口。
        # created_by 也不接受客户端伪造（AI 候选在 learn_from_ticket 中直写 store）。
        source_ticket_id = data.get("source_ticket_id") or None
        if source_ticket_id:
            source = ops_store().get_ticket(str(source_ticket_id))
            if not source:
                raise ValidationError("来源工单不存在")
            if not _is_admin(user) and source.get("created_by") != _username(user):
                raise ValidationError("只能引用自己的工单作为知识来源")
        ticket = ops_store().create_ticket({
            "kind": "knowledge",
            "status": "pending",
            "title": payload["title"],
            "request": str(data.get("request") or payload["content"])[:20_000],
            "knowledge_payload": payload,
            "risk_level": "low",
            "created_by": _username(user),
            "created_by_id": _user_id(user),
            "source_ticket_id": source_ticket_id,
            "untrusted": 1,
        })
        ops_store().append_event(
            ticket["id"], _username(user), "knowledge_submitted",
            {"title": payload["title"], "kind": payload["kind"]},
        )
        return ticket

    request = clean_text(
        data.get("request"), field="需求",
        max_chars=MAX_REQUEST_CHARS,
    )
    command = ""
    if data.get("command"):
        command = validate_command(data.get("command"))
    if not request and not command:
        raise ValidationError("请填写需求或命令")
    node = _require_node(
        data.get("target_node_id") or data.get("node_id") or "")
    exec_env = normalize_exec_env(data.get("exec_env"))
    params = normalize_params(data.get("params"))
    status = str(data.get("status") or default_status)
    if status not in {"draft", "pending"}:
        raise ValidationError("新工单只能是 draft 或 pending")
    ai_suggestion = data.get("ai_suggestion") or {}
    if not isinstance(ai_suggestion, dict):
        raise ValidationError("ai_suggestion 必须是对象")
    if len(json.dumps(ai_suggestion, ensure_ascii=False)) > 65_536:
        raise ValidationError("ai_suggestion 过大")

    ticket = ops_store().create_ticket({
        "kind": kind,
        "status": status,
        "title": clean_text(
            data.get("title") or _default_title(request, command),
            field="标题", max_bytes=500,
        ),
        "request": request,
        "command": command,
        "target_node_id": node["node_id"],
        "target_node_name": node["name"],
        "target_node_address": node["address"],
        "exec_env": exec_env,
        "params": params,
        "ai_suggestion": ai_suggestion,
        "ai_model": str(data.get("ai_model") or "")[:100],
        "risk_level": classify_risk(command),
        "created_by": _username(user),
        "created_by_id": _user_id(user),
        "untrusted": 1,
    })
    store = ops_store()
    store.append_event(ticket["id"], _username(user), "ticket_created", {
        "kind": kind,
        "status": status,
        "node_name": node["name"],
        "command_sha256": content_hash(command)[:16] if command else "",
    })
    if status == "pending":
        store.append_event(ticket["id"], _username(user), "ticket_submitted", {})
        if kind == "command":
            _notify_new_command(ticket)
    return ticket


def list_tickets(user: dict, *, status: str = "", kind: str = "",
                 search: str = "", limit: int = 100) -> list[dict]:
    include_all = _is_admin(user)
    rows = ops_store().list_tickets(
        status=status, kind=kind,
        created_by="" if include_all else _username(user),
        search=search, limit=limit, include_all=include_all,
    )
    result = []
    for row in rows:
        item = public_ticket(row)
        # 列表视图不传输完整执行输出/审批快照，详情页再按需获取。
        for key in ("exec_result", "approval_snapshot", "ai_suggestion",
                    "knowledge_payload"):
            item.pop(key, None)
        result.append(item)
    return result


def get_ticket(user: dict, ticket_id: str, *, include_events: bool = True) -> dict:
    ticket = ops_store().get_ticket(ticket_id)
    if not ticket:
        raise LookupError("工单不存在")
    if not _can_view(user, ticket):
        raise PermissionError("无权查看该工单")
    result = public_ticket(ticket)
    if include_events:
        result["events"] = ops_store().list_events(ticket_id)
    result["can_approve"] = _is_admin(user) and ticket["status"] == "pending"
    result["can_execute"] = (
        _is_admin(user) and ticket["status"] == "approved"
        and ticket.get("kind") != "knowledge"
    )
    result["can_modify"] = _can_modify(user, ticket)
    return result


def update_ticket(user: dict, ticket_id: str, data: dict) -> dict:
    store = ops_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        raise LookupError("工单不存在")
    if not _can_modify(user, ticket):
        raise PermissionError("工单已进入审批/执行流程，不能修改")

    reason = clean_text(data.get("reason") or data.get("admin_note"),
                        field="修改原因", max_bytes=4000)
    fields: dict = {}
    if "title" in data:
        fields["title"] = clean_text(
            data.get("title"), field="标题", max_bytes=500)
    if "request" in data:
        fields["request"] = clean_text(
            data.get("request"), field="需求",
            max_chars=MAX_REQUEST_CHARS,
        )
    if "command" in data:
        fields["command"] = validate_command(data.get("command"))
        fields["risk_level"] = classify_risk(fields["command"])
    if "exec_env" in data:
        fields["exec_env"] = normalize_exec_env(data.get("exec_env"))
    if "params" in data:
        fields["params"] = normalize_params(data.get("params"))
    target_node_id = data.get("target_node_id") or data.get("node_id")
    if target_node_id:
        node = _require_node(target_node_id)
        fields.update({
            "target_node_id": node["node_id"],
            "target_node_name": node["name"],
            "target_node_address": node["address"],
        })
    if ticket.get("kind") == "knowledge" and (
            "knowledge_payload" in data or any(
                key in data for key in ("title", "content", "tags", "kind"))):
        existing_payload = json_loads(ticket.get("knowledge_payload"), {})
        incoming_payload = data.get("knowledge_payload") or {}
        if not isinstance(incoming_payload, dict):
            raise ValidationError("知识内容必须是对象")
        merged_payload = {**existing_payload, **incoming_payload}
        for key in ("content", "tags", "kind"):
            if key in data:
                merged_payload[key] = data[key]
        if "title" in data:
            merged_payload["title"] = data["title"]
        payload = _knowledge_payload(merged_payload)
        fields["knowledge_payload"] = payload
        fields["title"] = payload["title"]

    if not fields:
        raise ValidationError("没有可修改的字段")
    if _is_admin(user) and not reason:
        raise ValidationError("管理员修改建议时必须填写原因")
    if reason:
        fields["admin_note"] = reason

    before = {
        key: content_hash(str(ticket.get(key) or ""))[:16]
        for key in fields
    }
    try:
        store.update_ticket(ticket_id, **fields)
    except sqlite3.IntegrityError as exc:
        raise ValidationError("工单状态已变化，不能修改") from exc
    store.append_event(ticket_id, _username(user), "ticket_updated", {
        "fields": sorted(fields),
        "before": before,
        "reason": reason,
    })
    return get_ticket(user, ticket_id)


def submit_ticket(user: dict, ticket_id: str) -> dict:
    store = ops_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        raise LookupError("工单不存在")
    if not _can_view(user, ticket):
        raise PermissionError("无权操作该工单")
    if ticket["status"] != "draft":
        raise ValidationError("只有草稿工单可以提交审批")
    if ticket["kind"] in {"command", "consult"}:
        if not ticket.get("target_node_id"):
            raise ValidationError("请选择目标节点")
        _require_node(ticket["target_node_id"])
        if not (ticket.get("request") or ticket.get("command")):
            raise ValidationError("请填写需求或命令")
    changed = store.transition(
        ticket_id, ("draft",), "pending", expected_updated_at=ticket["updated_at"],
        submitted_at=now(),
    )
    if not changed:
        raise ValidationError("提交失败，请刷新后重试")
    store.append_event(ticket_id, _username(user), "ticket_submitted", {})
    if ticket.get("kind") == "command":
        _notify_new_command(store.get_ticket(ticket_id) or ticket)
    return get_ticket(user, ticket_id)


def approve_ticket(user: dict, ticket_id: str, data: dict) -> dict:
    """管理员批准：冻结完整快照；知识工单直接入知识库。"""
    if not _is_admin(user):
        raise PermissionError("只有管理员可以批准工单")
    store = ops_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        raise LookupError("工单不存在")
    if ticket["status"] != "pending":
        raise ValidationError(f"工单状态为 {ticket['status']}，不能批准")

    reason = clean_text(
        data.get("reason") or data.get("approval_reason"),
        field="批准原因", max_bytes=4000, allow_empty=False,
    )

    if ticket["kind"] == "knowledge":
        # 管理员可在批准时带上最后一次编辑；编辑仍走 pending 状态校验。
        if any(key in data for key in (
                "knowledge_payload", "title", "content", "tags", "kind")):
            update_ticket(user, ticket_id, {**data, "reason": reason})
            ticket = store.get_ticket(ticket_id)
        return _approve_knowledge(user, ticket, reason)

    # 允许管理员在批准的同时做最后一次修改；修改和状态冻结在同一次
    # transition 中完成，批准后由数据库触发器禁止再改。
    fields: dict = {}
    if data.get("command") is not None:
        fields["command"] = validate_command(data.get("command"))
    if data.get("exec_env") is not None:
        fields["exec_env"] = normalize_exec_env(data.get("exec_env"))
    if data.get("params") is not None:
        fields["params"] = normalize_params(data.get("params"))
    target_node_id = data.get("target_node_id") or data.get("node_id")
    if target_node_id:
        node = _require_node(target_node_id)
        fields.update({
            "target_node_id": node["node_id"],
            "target_node_name": node["name"],
            "target_node_address": node["address"],
        })
    merged = {**ticket, **fields}
    if not merged.get("command"):
        raise ValidationError("批准前必须填写完整命令")
    if not merged.get("target_node_id"):
        raise ValidationError("批准前必须选择目标节点")
    node = _require_node(merged["target_node_id"])
    # 以当前节点池刷新地址，并把刷新结果放入快照。
    merged.update({
        "target_node_id": node["node_id"],
        "target_node_name": node["name"],
        "target_node_address": node["address"],
    })
    merged["exec_env"] = normalize_exec_env(
        merged.get("exec_env") or {})
    merged["params"] = normalize_params(merged.get("params") or {})
    merged["risk_level"] = classify_risk(merged["command"])

    from .security import build_approval_snapshot

    snapshot, snapshot_hash, token = build_approval_snapshot(
        merged, _username(user))
    approved_at = snapshot["approved_at"]
    changed = store.transition(
        ticket_id, ("pending",), "approved",
        expected_updated_at=ticket["updated_at"],
        command=merged["command"],
        target_node_id=merged["target_node_id"],
        target_node_name=merged["target_node_name"],
        target_node_address=merged["target_node_address"],
        exec_env=merged["exec_env"],
        params=merged["params"],
        risk_level=merged["risk_level"],
        approval_snapshot=snapshot,
        approval_hash=snapshot_hash,
        approval_token=token,
        approved_by=_username(user),
        approved_at=approved_at,
        approval_reason=reason,
        admin_note=reason,
    )
    if not changed:
        raise ValidationError("工单已被其他管理员处理，请刷新后重试")
    store.append_event(ticket_id, _username(user), "ticket_approved", {
        "reason": reason,
        "node_id": merged["target_node_id"],
        "node_name": merged["target_node_name"],
        "command_sha256": content_hash(merged["command"])[:16],
        "approval_hash": snapshot_hash,
        "exec_env": merged["exec_env"],
        "params": merged["params"],
    })
    result = get_ticket(user, ticket_id)
    result["approval_snapshot"] = snapshot
    raw_execute = data.get("execute", False)
    execute = raw_execute is True or str(raw_execute).strip().lower() in {
        "1", "true", "yes", "on",
    }
    if execute:
        start_execution(ticket_id, _username(user))
        result = get_ticket(user, ticket_id)
        result["execution_started"] = True
    return result


def _approve_knowledge(user: dict, ticket: dict, reason: str) -> dict:
    store = ops_store()
    payload = json_loads(ticket.get("knowledge_payload"), {})
    if not payload.get("title") or not payload.get("content"):
        raise ValidationError("知识内容不完整，无法加入知识库")
    digest = payload.get("content_hash") or content_hash(
        str(payload["title"]).lower(), str(payload["content"]).lower())
    if store.find_knowledge_hash(digest):
        raise ValidationError("知识库中已存在相同内容，请驳回该候选")
    knowledge = store.create_knowledge({
        "ticket_id": ticket["id"],
        "title": payload["title"],
        "content": payload["content"],
        "kind": payload.get("kind") or "experience",
        "tags": payload.get("tags") or [],
        "source_ticket_ids": payload.get("source_ticket_ids")
        or ([ticket["source_ticket_id"]] if ticket.get("source_ticket_id") else []),
        "content_hash": digest,
        "created_by": ticket.get("created_by", ""),
        "approved_by": _username(user),
        "created_at": ticket.get("created_at"),
        "approved_at": now(),
    })
    if not knowledge:
        raise ValidationError("知识入库失败（可能已存在）")
    changed = store.transition(
        ticket["id"], ("pending",), "approved",
        expected_updated_at=ticket["updated_at"],
        approved_by=_username(user), approved_at=now(),
        approval_reason=reason, admin_note=reason,
    )
    if not changed:
        raise ValidationError("工单状态已变化，请刷新后重试")
    store.append_event(ticket["id"], _username(user), "knowledge_approved", {
        "reason": reason,
        "knowledge_id": knowledge["id"],
        "title": knowledge["title"],
    })
    result = get_ticket(user, ticket["id"])
    result["knowledge"] = knowledge
    return result


def reject_ticket(user: dict, ticket_id: str, data: dict) -> dict:
    if not _is_admin(user):
        raise PermissionError("只有管理员可以驳回工单")
    reason = clean_text(
        data.get("reason") or data.get("reject_reason"),
        field="驳回原因", max_bytes=4000, allow_empty=False,
    )
    store = ops_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        raise LookupError("工单不存在")
    changed = store.transition(
        ticket_id, ("pending",), "rejected",
        expected_updated_at=ticket["updated_at"],
        rejected_by=_username(user), rejected_at=now(),
        reject_reason=reason, admin_note=reason,
    )
    if not changed:
        raise ValidationError("只有 pending 工单可以驳回")
    store.append_event(ticket_id, _username(user), "ticket_rejected", {
        "reason": reason,
    })
    schedule_learning(ticket_id)
    return get_ticket(user, ticket_id)


def cancel_ticket(user: dict, ticket_id: str) -> dict:
    store = ops_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        raise LookupError("工单不存在")
    if not _can_view(user, ticket):
        raise PermissionError("无权操作该工单")
    changed = store.transition(
        ticket_id, ("draft", "pending"), "cancelled",
        expected_updated_at=ticket["updated_at"],
    )
    if not changed:
        raise ValidationError("只有草稿/待审批工单可以取消")
    store.append_event(ticket_id, _username(user), "ticket_cancelled", {})
    return get_ticket(user, ticket_id)


def execute_ticket(user: dict, ticket_id: str) -> dict:
    """管理员触发执行；执行器内部再次校验状态、快照和一次性 token。"""
    if not _is_admin(user):
        raise PermissionError("只有管理员可以执行特权工单")
    ticket = ops_store().get_ticket(ticket_id)
    if not ticket:
        raise LookupError("工单不存在")
    if ticket.get("kind") == "knowledge":
        raise ValidationError("知识工单不能执行")
    if ticket["status"] != "approved":
        raise ValidationError(f"工单状态为 {ticket['status']}，不能执行")
    ops_store().append_event(
        ticket_id, _username(user), "execution_requested", {},
    )
    start_execution(ticket_id, _username(user))
    return get_ticket(user, ticket_id)


# ═══════════════════════════════════════════════════════════════
# AI 命令建议（只返回建议，不创建工单、不执行）
# ═══════════════════════════════════════════════════════════════

def assistant_suggest(user: dict, data: dict, on_event=None) -> dict:
    """根据新建工单表单里的需求 + 节点环境生成命令建议。

    该接口不创建工单，也不触发执行；用户确认并提交工单后，命令仍需
    管理员审批。前端会把建议随工单一并保存，便于后续学习“管理员修正”。
    """
    request = clean_text(
        data.get("request") or data.get("message"),
        field="需求", allow_empty=False, max_chars=MAX_REQUEST_CHARS,
    )
    node = _require_node(str(data.get("node_id") or "").strip())
    store = ops_store()

    history_ids = store.search_ticket_ids(request, limit=5)
    history = []
    for ticket_id in history_ids:
        item = store.get_ticket(ticket_id)
        if not item:
            continue
        # 普通用户的 AI 上下文只包含自己的工单，避免跨用户泄露。
        if _is_admin(user) or item.get("created_by") == _username(user):
            history.append(item)
    if not history:
        history = store.list_tickets(
            created_by="" if _is_admin(user) else _username(user),
            include_all=_is_admin(user), limit=5,
        )
    knowledge_ids = store.search_knowledge_ids(request, limit=5)
    knowledge = [store.get_knowledge(item) for item in knowledge_ids]
    knowledge = [item for item in knowledge if item]

    suggestion = suggest_command(
        request=request, node=node, history=history,
        knowledge=knowledge, username=_username(user),
        tool_user=tool_user_for(user, node),
        on_event=on_event,
    )
    return {"suggestion": suggestion, "warning": ""}


# ═══════════════════════════════════════════════════════════════
# 知识库 / 自学习
# ═══════════════════════════════════════════════════════════════

def list_knowledge(*, search: str = "", kind: str = "",
                   limit: int = 100) -> list[dict]:
    return ops_store().list_knowledge(search=search, kind=kind, limit=limit)


def get_knowledge(knowledge_id: str) -> dict:
    item = ops_store().get_knowledge(knowledge_id)
    if not item:
        raise LookupError("知识条目不存在")
    return item


def update_knowledge(user: dict, knowledge_id: str, data: dict) -> dict:
    """管理员直接修改已入库知识，不再走审批流程。"""
    if not _is_admin(user):
        raise PermissionError("只有管理员可以修改知识库")
    store = ops_store()
    item = store.get_knowledge(knowledge_id)
    if not item:
        raise LookupError("知识条目不存在")
    merged = {
        "title": data.get("title", item.get("title")),
        "content": data.get("content", item.get("content")),
        "kind": data.get("kind", item.get("kind")),
        "tags": data.get("tags", item.get("tags") or []),
        "source_ticket_ids": item.get("source_ticket_ids") or [],
    }
    payload = _knowledge_payload(merged)
    duplicate = store.find_knowledge_hash(
        payload["content_hash"], exclude_id=knowledge_id)
    if duplicate:
        raise ValidationError("知识库中已存在相同内容")
    updated = store.update_knowledge(
        knowledge_id,
        title=payload["title"],
        content=payload["content"],
        kind=payload["kind"],
        tags=payload["tags"],
        content_hash=payload["content_hash"],
    )
    if not updated:
        raise ValidationError("知识更新失败（内容可能重复）")
    if item.get("ticket_id"):
        store.append_event(
            item["ticket_id"], _username(user), "knowledge_updated",
            {"title": updated["title"], "kind": updated["kind"]},
        )
    return updated


def delete_knowledge(user: dict, knowledge_id: str) -> None:
    """管理员直接删除知识库条目，不再走审批流程。"""
    if not _is_admin(user):
        raise PermissionError("只有管理员可以删除知识库")
    store = ops_store()
    item = store.get_knowledge(knowledge_id)
    if not item:
        raise LookupError("知识条目不存在")
    if not store.delete_knowledge(knowledge_id):
        raise LookupError("知识条目不存在")
    if item.get("ticket_id"):
        store.append_event(
            item["ticket_id"], _username(user), "knowledge_deleted",
            {"title": item.get("title", "")},
        )


def _already_learning(ticket_id: str) -> bool:
    rows = ops_store().list_tickets(
        kind="knowledge", limit=100, include_all=True,
    )
    for row in rows:
        if row.get("source_ticket_id") == ticket_id:
            return True
    return False


def learn_from_ticket(ticket_id: str, *, force: bool = False) -> list[dict]:
    """从终态工单提炼知识候选；候选本身也是 pending 工单，需管理员批准。"""
    store = ops_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        return []
    # 知识候选本身不再作为学习来源，避免“学习元知识”造成污染。
    if ticket.get("kind") == "knowledge":
        return []
    if ticket.get("status") not in {"rejected", "succeeded", "failed"}:
        return []
    # 成功的常规命令不自动上报，避免每张命令工单都产生经验候选；
    # 管理员可以用 force=True 手动提炼成功案例。
    if not force and ticket.get("status") == "succeeded":
        return []
    # 只沉淀带具体命令的经验；纯咨询/空命令不生成知识候选。
    if not str(ticket.get("command") or "").strip():
        return []
    if not force and _already_learning(ticket_id):
        return []
    events = store.list_events(ticket_id)
    reason = ticket.get("reject_reason") or ticket.get("approval_reason") or ""
    entries = extract_knowledge(
        ticket=ticket, events=events, reason=reason,
    )
    created = []
    for entry in entries:
        entry_command = str(
            entry.get("command") or ticket.get("command") or "").strip()
        if not entry_command:
            continue
        payload = {
            "title": entry["title"],
            "command": entry_command,
            "content": entry["content"],
            "kind": entry.get("kind") or "experience",
            "tags": entry.get("tags") or [],
            "source_ticket_ids": [ticket_id],
            "content_hash": content_hash(
                entry["title"].lower(), entry["content"].lower()),
            "confidence": entry.get("confidence") or "",
        }
        if store.find_knowledge_hash(payload["content_hash"]):
            continue
        # 避免同一来源重复生成相同候选。
        duplicate = any(
            json_loads(row.get("knowledge_payload"), {}).get("content_hash")
            == payload["content_hash"]
            for row in store.list_tickets(
                kind="knowledge", status="pending", limit=100,
                include_all=True,
            )
            if row.get("source_ticket_id") == ticket_id
        )
        if duplicate:
            continue
        candidate = store.create_ticket({
            "kind": "knowledge",
            "status": "pending",
            "title": payload["title"],
            "request": f"AI 从工单 {ticket_id} 提炼的运维经验",
            "knowledge_payload": payload,
            "risk_level": "low",
            "created_by": "NeuOps AI",
            "created_by_id": "system:ai",
            "source_ticket_id": ticket_id,
            "ai_model": ai_model_name() if ai_configured() else "fallback",
            "untrusted": 1,
        })
        store.append_event(candidate["id"], "system:ai", "learning_candidate", {
            "source_ticket_id": ticket_id,
            "confidence": payload["confidence"],
        })
        created.append(candidate)
    return created


def schedule_learning(ticket_id: str) -> None:
    if not _auto_learn():
        return
    if _learn_sync():
        learn_from_ticket(ticket_id)
        return

    def _job() -> None:
        try:
            learn_from_ticket(ticket_id)
        except Exception:
            logger.exception("工单 %s 自学习失败", ticket_id)

    threading.Thread(
        target=_job, daemon=True, name=f"rroot-learn-{ticket_id[:8]}",
    ).start()


def stats() -> dict:
    result = ops_store().stats()
    result["ai_configured"] = ai_configured()
    result["ai_model"] = ai_model_name() if ai_configured() else ""
    result["root_user"] = ROOT_USER
    return result
