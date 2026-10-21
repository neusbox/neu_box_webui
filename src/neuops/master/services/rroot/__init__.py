"""Rroot（Restricted Root）：NeuOps 的 AI 辅助运维工单子系统。

模块边界：
  * ``security``  输入校验、审批快照、prompt injection 防护、命令包装；
  * ``store``     工单/事件/知识库持久化与 FTS5 检索；
  * ``ai``        OpenAI 兼容 API、建议生成、知识提炼；
  * ``executor``  唯一允许向 worker 提交 root 命令的执行器；
  * ``service``   工单生命周期、审批、AI 命令建议和自学习编排。

对外 HTTP 接口在 ``neuops.master.api.ops``，AI 与普通用户都不能直接
调用 ``executor.start_execution``。
"""

from .service import (  # noqa: F401
    approve_ticket,
    assistant_suggest,
    cancel_ticket,
    create_ticket,
    execute_ticket,
    get_knowledge,
    get_ticket,
    learn_from_ticket,
    list_knowledge,
    list_tickets,
    reject_ticket,
    schedule_learning,
    stats,
    submit_ticket,
    update_ticket,
)

__all__ = [
    "approve_ticket",
    "assistant_suggest",
    "cancel_ticket",
    "create_ticket",
    "execute_ticket",
    "get_knowledge",
    "get_ticket",
    "learn_from_ticket",
    "list_knowledge",
    "list_tickets",
    "reject_ticket",
    "schedule_learning",
    "stats",
    "submit_ticket",
    "update_ticket",
]
