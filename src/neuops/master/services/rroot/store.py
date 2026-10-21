"""Rroot 持久化层：工单、事件、知识库与 FTS5 检索。

所有 SQL 都集中在这里；AII 客户端和特权执行器只通过 service 层调用，
不直接拼 SQL。历史工单属于不可信数据，读取时不做任何 HTML/指令解释。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Iterable

from neuops.master.services.db import Database

from .security import (
    KNOWLEDGE_KINDS,
    TICKET_KINDS,
    TICKET_STATUSES,
    ValidationError,
    content_hash,
    json_loads,
    new_id,
    now,
)


_TICKET_JSON_FIELDS = (
    "exec_env", "params", "ai_suggestion", "knowledge_payload",
    "approval_snapshot", "exec_result",
)
_TICKET_UPDATE_FIELDS = {
    "status", "title", "request", "command", "target_node_id",
    "target_node_name", "target_node_address", "exec_env", "params",
    "ai_suggestion", "ai_model", "risk_level", "knowledge_payload",
    "admin_note", "approval_snapshot", "approval_hash", "approval_token",
    "submitted_at", "approved_by", "approved_at", "approval_reason",
    "rejected_by", "rejected_at", "reject_reason", "executed_by",
    "executed_at", "worker_task_id", "execution_status", "exec_result",
    "exec_log_path", "source_ticket_id", "untrusted",
}


def _db() -> Database:
    return Database.get_instance()


def _decode_ticket(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    ticket = dict(row)
    for key in _TICKET_JSON_FIELDS:
        if key in ticket:
            default = {} if key not in {"ai_suggestion"} else {}
            ticket[key] = json_loads(ticket[key], default)
    return ticket


def decode_ticket(ticket: dict | None) -> dict | None:
    """把数据库行的 JSON 字符串解码为 Python 对象（公开辅助函数）。"""
    if ticket is None:
        return None
    result = dict(ticket)
    for key in _TICKET_JSON_FIELDS:
        if key in result:
            result[key] = json_loads(result[key], {})
    return result


def _terms(text: str) -> tuple[list[str], list[str]]:
    """拆分检索词：返回 (可走 FTS 的长词, 只能 LIKE 的短词)。"""
    raw = re.findall(
        r"[A-Za-z0-9_./:@-]{2,}|[\u3400-\u9fff]{2,}", text or "",
    )
    long_terms: list[str] = []
    short_terms: list[str] = []
    seen: set[str] = set()
    for term in raw:
        term = term.strip().lower()
        if not term or term in seen:
            continue
        seen.add(term)
        # trigram tokenizer 至少需要 3 个字符；中文两字词回退 LIKE。
        if len(term) >= 3:
            long_terms.append(term)
        else:
            short_terms.append(term)
    return long_terms[:8], short_terms[:8]


def _fts_query(text: str) -> str:
    long_terms, _ = _terms(text)
    if not long_terms:
        return ""
    return " OR ".join(
        '"' + term.replace('"', '""') + '"' for term in long_terms
    )


def _like_clause(column: str, term: str) -> tuple[str, str]:
    return f"{column} LIKE ?", f"%{term}%"


class OpsStore:
    """无状态 store；所有方法每次取 Database 单例，便于测试替换。"""

    # ── 工单 ──────────────────────────────────────────────────

    def create_ticket(self, data: dict) -> dict:
        kind = data.get("kind", "command")
        if kind not in TICKET_KINDS:
            raise ValidationError(f"未知工单类型: {kind}")
        status = data.get("status", "draft")
        if status not in TICKET_STATUSES:
            raise ValidationError(f"未知工单状态: {status}")
        current = now()
        ticket_id = data.get("id") or new_id("op_")
        fields = {
            "id": ticket_id,
            "kind": kind,
            "status": status,
            "title": str(data.get("title", "") or ""),
            "request": str(data.get("request", "") or ""),
            "command": str(data.get("command", "") or ""),
            "target_node_id": str(data.get("target_node_id", "") or ""),
            "target_node_name": str(data.get("target_node_name", "") or ""),
            "target_node_address": str(data.get("target_node_address", "") or ""),
            "exec_env": json.dumps(data.get("exec_env") or {}, ensure_ascii=False),
            "params": json.dumps(data.get("params") or {}, ensure_ascii=False),
            "ai_suggestion": json.dumps(
                data.get("ai_suggestion") or {}, ensure_ascii=False),
            "ai_model": str(data.get("ai_model", "") or ""),
            "risk_level": str(data.get("risk_level", "unknown") or "unknown"),
            "knowledge_payload": json.dumps(
                data.get("knowledge_payload") or {}, ensure_ascii=False),
            "admin_note": str(data.get("admin_note", "") or ""),
            "created_by": str(data.get("created_by", "") or ""),
            "created_by_id": str(data.get("created_by_id", "") or ""),
            "created_at": current,
            "updated_at": current,
            "source_ticket_id": data.get("source_ticket_id") or None,
            "untrusted": int(bool(data.get("untrusted", 1))),
        }
        if status == "pending":
            fields["submitted_at"] = current
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        with _db().connection() as conn:
            conn.execute(
                f"INSERT INTO ops_tickets ({columns}) VALUES ({placeholders})",
                list(fields.values()),
            )
            conn.commit()
        self.reindex_ticket(ticket_id)
        return self.get_ticket(ticket_id) or {}

    def get_ticket(self, ticket_id: str) -> dict | None:
        with _db().connection() as conn:
            row = conn.execute(
                "SELECT * FROM ops_tickets WHERE id=?", (ticket_id,),
            ).fetchone()
        return decode_ticket(row)

    def list_tickets(self, *, status: str = "", kind: str = "",
                     created_by: str = "", search: str = "",
                     limit: int = 100,
                     include_all: bool = False) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            if status not in TICKET_STATUSES:
                raise ValidationError(f"未知状态: {status}")
            conditions.append("t.status = ?")
            params.append(status)
        if kind:
            if kind not in TICKET_KINDS:
                raise ValidationError(f"未知类型: {kind}")
            conditions.append("t.kind = ?")
            params.append(kind)
        if created_by and not include_all:
            conditions.append("t.created_by = ?")
            params.append(created_by)

        if search and search.strip():
            ids = self.search_ticket_ids(search, limit=limit * 3)
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            conditions.append(f"t.id IN ({placeholders})")
            params.extend(ids)
            order = "t.updated_at DESC"
        else:
            order = "t.created_at DESC"

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = (
            f"SELECT t.* FROM ops_tickets t{where} "
            f"ORDER BY {order} LIMIT ?"
        )
        params.append(limit)
        with _db().connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [decode_ticket(row) for row in rows]

    def search_ticket_ids(self, text: str, limit: int = 50) -> list[str]:
        """FTS5 优先；短词回退 LIKE，保证中文两字词也能搜到。"""
        text = (text or "").strip()
        if not text:
            return []
        limit = max(1, min(int(limit or 50), 200))
        found: list[str] = []
        seen: set[str] = set()

        def _add(rows: Iterable[sqlite3.Row]):
            for row in rows:
                value = str(row[0])
                if value not in seen:
                    seen.add(value)
                    found.append(value)

        long_terms, short_terms = _terms(text)
        query = _fts_query(text)
        with _db().connection() as conn:
            if query:
                try:
                    rows = conn.execute(
                        "SELECT f.ticket_id FROM ops_tickets_fts f "
                        "WHERE ops_tickets_fts MATCH ? ORDER BY rank LIMIT ?",
                        (query, limit),
                    ).fetchall()
                    _add(rows)
                except sqlite3.OperationalError:
                    pass
            if len(found) < limit:
                # 长词没有命中（或 trigram 不支持短词）时，用 LIKE 兜底。
                terms = short_terms or long_terms[:4]
                for term in terms:
                    if len(found) >= limit:
                        break
                    clause, value = _like_clause("t.title", term)
                    rows = conn.execute(
                        "SELECT t.id FROM ops_tickets t WHERE "
                        f"{clause} OR t.request LIKE ? OR t.command LIKE ? "
                        "OR t.ai_suggestion LIKE ? OR t.admin_note LIKE ? "
                        "OR t.approval_reason LIKE ? OR t.reject_reason LIKE ? "
                        "ORDER BY t.updated_at DESC LIMIT ?",
                        (value, value, value, value, value, value, value,
                         limit - len(found)),
                    ).fetchall()
                    _add(rows)
        return found[:limit]

    def update_ticket(self, ticket_id: str, **fields) -> bool:
        allowed = {
            key: value for key, value in fields.items()
            if key in _TICKET_UPDATE_FIELDS
        }
        if not allowed:
            return False
        allowed["updated_at"] = now()
        for key in ("exec_env", "params", "ai_suggestion", "knowledge_payload",
                    "approval_snapshot", "exec_result"):
            if key in allowed and not isinstance(allowed[key], str):
                allowed[key] = json.dumps(
                    allowed[key], ensure_ascii=False, sort_keys=True)
        set_clause = ", ".join(f"{key}=?" for key in allowed)
        values = list(allowed.values()) + [ticket_id]
        with _db().connection() as conn:
            cursor = conn.execute(
                f"UPDATE ops_tickets SET {set_clause} WHERE id=?",
                values,
            )
            conn.commit()
            changed = cursor.rowcount > 0
        if changed:
            self.reindex_ticket(ticket_id)
        return changed

    def transition(self, ticket_id: str, from_statuses: Iterable[str],
                   to_status: str, *, expected_updated_at: float | None = None,
                   **fields) -> bool:
        """原子状态迁移；可带 updated_at 做乐观并发校验。"""
        from_statuses = tuple(from_statuses)
        if not from_statuses:
            return False
        allowed = {
            key: value for key, value in fields.items()
            if key in _TICKET_UPDATE_FIELDS
        }
        allowed["status"] = to_status
        allowed["updated_at"] = now()
        for key in ("exec_env", "params", "ai_suggestion", "knowledge_payload",
                    "approval_snapshot", "exec_result"):
            if key in allowed and not isinstance(allowed[key], str):
                allowed[key] = json.dumps(
                    allowed[key], ensure_ascii=False, sort_keys=True)
        set_clause = ", ".join(f"{key}=?" for key in allowed)
        placeholders = ",".join("?" for _ in from_statuses)
        where = f"id=? AND status IN ({placeholders})"
        values = list(allowed.values()) + [ticket_id, *from_statuses]
        if expected_updated_at is not None:
            where += " AND updated_at=?"
            values.append(float(expected_updated_at))
        with _db().connection() as conn:
            cursor = conn.execute(
                f"UPDATE ops_tickets SET {set_clause} WHERE {where}",
                values,
            )
            conn.commit()
            changed = cursor.rowcount == 1
        if changed:
            self.reindex_ticket(ticket_id)
        return changed

    def claim_for_execution(self, ticket_id: str, actor: str) -> bool:
        """一次性消费审批：仅 approved + 存在 token 时迁移到 executing。"""
        current = now()
        with _db().connection() as conn:
            cursor = conn.execute(
                "UPDATE ops_tickets SET status='executing', "
                "execution_status='executing', executed_by=?, "
                "executed_at=?, approval_token=NULL, updated_at=? "
                "WHERE id=? AND status='approved' "
                "AND approval_token IS NOT NULL AND approval_token != ''",
                (actor, current, current, ticket_id),
            )
            conn.commit()
            changed = cursor.rowcount == 1
        if changed:
            self.reindex_ticket(ticket_id)
        return changed

    def append_event(self, ticket_id: str, actor: str, action: str,
                     detail: dict | None = None) -> None:
        with _db().connection() as conn:
            conn.execute(
                "INSERT INTO ops_ticket_events "
                "(ticket_id, actor, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ticket_id, actor or "", action,
                 json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
                 now()),
            )
            conn.commit()

    def list_events(self, ticket_id: str, limit: int = 200) -> list[dict]:
        with _db().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM ops_ticket_events WHERE ticket_id=? "
                "ORDER BY id ASC LIMIT ?",
                (ticket_id, max(1, min(limit, 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["detail"] = json_loads(item.get("detail"), {})
            result.append(item)
        return result

    def reindex_ticket(self, ticket_id: str) -> None:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return
        exec_summary = " ".join(
            str(part or "") for part in (
                ticket.get("execution_status"),
                json_loads(ticket.get("exec_result"), {}).get("stderr"),
                json_loads(ticket.get("exec_result"), {}).get("stdout"),
            )
        )[:8000]
        with _db().connection() as conn:
            conn.execute(
                "DELETE FROM ops_tickets_fts WHERE ticket_id=?", (ticket_id,),
            )
            conn.execute(
                "INSERT INTO ops_tickets_fts "
                "(ticket_id, title, request, command, ai_suggestion, "
                " admin_note, approval_reason, reject_reason, exec_summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticket_id,
                    str(ticket.get("title", "") or ""),
                    str(ticket.get("request", "") or ""),
                    str(ticket.get("command", "") or ""),
                    json.dumps(ticket.get("ai_suggestion") or {},
                               ensure_ascii=False),
                    str(ticket.get("admin_note", "") or ""),
                    str(ticket.get("approval_reason", "") or ""),
                    str(ticket.get("reject_reason", "") or ""),
                    exec_summary,
                ),
            )
            conn.commit()

    def recent_tickets(self, limit: int = 8, exclude_id: str = "") -> list[dict]:
        with _db().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM ops_tickets WHERE id != ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (exclude_id, max(1, min(limit, 50))),
            ).fetchall()
        return [decode_ticket(row) for row in rows]

    def stats(self) -> dict:
        with _db().connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM ops_tickets "
                "GROUP BY status"
            ).fetchall()
            knowledge = conn.execute(
                "SELECT COUNT(*) FROM ops_knowledge"
            ).fetchone()[0]
        return {
            "by_status": {str(r["status"]): int(r["n"]) for r in rows},
            "knowledge": int(knowledge),
        }

    # ── 知识库 ────────────────────────────────────────────────

    def create_knowledge(self, data: dict) -> dict | None:
        kind = str(data.get("kind") or "experience")
        if kind not in KNOWLEDGE_KINDS:
            kind = "experience"
        title = str(data.get("title") or "").strip()
        content = str(data.get("content") or "").strip()
        if not title or not content:
            return None
        digest = str(data.get("content_hash") or "") or content_hash(
            title.lower(), content.lower(),
        )
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        tags = [str(tag).strip()[:64] for tag in tags if str(tag).strip()][:12]
        source_ids = data.get("source_ticket_ids") or []
        if not isinstance(source_ids, list):
            source_ids = [str(source_ids)]
        current = now()
        knowledge_id = data.get("id") or content_hash(digest, str(current))[:16]
        try:
            with _db().connection() as conn:
                conn.execute(
                    "INSERT INTO ops_knowledge "
                    "(id, ticket_id, title, content, kind, tags, "
                    " source_ticket_ids, content_hash, created_by, "
                    " approved_by, created_at, approved_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        knowledge_id, data.get("ticket_id"),
                        title[:500], content[:20_000], kind,
                        json.dumps(tags, ensure_ascii=False),
                        json.dumps(source_ids, ensure_ascii=False),
                        digest, str(data.get("created_by") or ""),
                        str(data.get("approved_by") or ""),
                        float(data.get("created_at") or current),
                        float(data.get("approved_at") or current),
                        current,
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            return None
        self.reindex_knowledge(knowledge_id)
        return self.get_knowledge(knowledge_id)

    def get_knowledge(self, knowledge_id: str) -> dict | None:
        with _db().connection() as conn:
            row = conn.execute(
                "SELECT * FROM ops_knowledge WHERE id=?", (knowledge_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = json_loads(item.get("tags"), [])
        item["source_ticket_ids"] = json_loads(
            item.get("source_ticket_ids"), [])
        return item

    def list_knowledge(self, *, search: str = "", kind: str = "",
                       limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        conditions: list[str] = []
        params: list[Any] = []
        if kind:
            if kind not in KNOWLEDGE_KINDS:
                raise ValidationError(f"未知知识类型: {kind}")
            conditions.append("k.kind = ?")
            params.append(kind)
        if search and search.strip():
            ids = self.search_knowledge_ids(search, limit=limit * 3)
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            conditions.append(f"k.id IN ({placeholders})")
            params.extend(ids)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        with _db().connection() as conn:
            rows = conn.execute(
                f"SELECT k.* FROM ops_knowledge k{where} "
                "ORDER BY k.approved_at DESC LIMIT ?",
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = json_loads(item.get("tags"), [])
            item["source_ticket_ids"] = json_loads(
                item.get("source_ticket_ids"), [])
            result.append(item)
        return result

    def search_knowledge_ids(self, text: str, limit: int = 50) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        limit = max(1, min(int(limit or 50), 200))
        found: list[str] = []
        seen: set[str] = set()

        def _add(rows):
            for row in rows:
                value = str(row[0])
                if value not in seen:
                    seen.add(value)
                    found.append(value)

        long_terms, short_terms = _terms(text)
        query = _fts_query(text)
        with _db().connection() as conn:
            if query:
                try:
                    _add(conn.execute(
                        "SELECT f.knowledge_id FROM ops_knowledge_fts f "
                        "WHERE ops_knowledge_fts MATCH ? ORDER BY rank LIMIT ?",
                        (query, limit),
                    ).fetchall())
                except sqlite3.OperationalError:
                    pass
            if len(found) < limit:
                terms = short_terms or long_terms[:4]
                for term in terms:
                    if len(found) >= limit:
                        break
                    like = f"%{term}%"
                    _add(conn.execute(
                        "SELECT k.id FROM ops_knowledge k WHERE "
                        "k.title LIKE ? OR k.content LIKE ? OR k.tags LIKE ? "
                        "ORDER BY k.approved_at DESC LIMIT ?",
                        (like, like, like, limit - len(found)),
                    ).fetchall())
        return found[:limit]

    def reindex_knowledge(self, knowledge_id: str) -> None:
        item = self.get_knowledge(knowledge_id)
        if not item:
            return
        with _db().connection() as conn:
            conn.execute(
                "DELETE FROM ops_knowledge_fts WHERE knowledge_id=?",
                (knowledge_id,),
            )
            conn.execute(
                "INSERT INTO ops_knowledge_fts "
                "(knowledge_id, title, content, tags) VALUES (?, ?, ?, ?)",
                (
                    knowledge_id, item.get("title", ""),
                    item.get("content", ""),
                    json.dumps(item.get("tags") or [], ensure_ascii=False),
                ),
            )
            conn.commit()

    def find_knowledge_hash(self, digest: str) -> dict | None:
        with _db().connection() as conn:
            row = conn.execute(
                "SELECT * FROM ops_knowledge WHERE content_hash=?", (digest,),
            ).fetchone()
        return self.get_knowledge(str(row["id"])) if row else None


def ops_store() -> OpsStore:
    return OpsStore()
