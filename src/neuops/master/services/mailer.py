"""SMTP 邮件服务（默认适配 QQ 邮箱）与管理员新工单通知。

QQ 邮箱需要开启 SMTP 并使用“授权码”而不是登录密码：
  NEUOPS_SMTP_HOST=smtp.qq.com
  NEUOPS_SMTP_PORT=465
  NEUOPS_SMTP_USER=xxx@qq.com
  NEUOPS_SMTP_PASSWORD=QQ邮箱授权码
  NEUOPS_SMTP_FROM=xxx@qq.com
  NEUOPS_SMTP_USE_SSL=1
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage

from neuops.config import env_int, env_text
from neuops.master.services.db import Database


logger = logging.getLogger("master.mailer")


class MailError(RuntimeError):
    """邮件发送失败。"""


def _env_bool(name: str, default: bool) -> bool:
    return env_text(name, "1" if default else "0").lower() in {
        "1", "true", "yes", "on",
    }


class Mailer:
    def __init__(self) -> None:
        self.host = env_text("NEUOPS_SMTP_HOST", "smtp.qq.com")
        self.port = env_int("NEUOPS_SMTP_PORT", 465)
        self.user = env_text("NEUOPS_SMTP_USER")
        self.password = env_text("NEUOPS_SMTP_PASSWORD")
        self.from_addr = env_text("NEUOPS_SMTP_FROM", self.user)
        self.use_ssl = _env_bool("NEUOPS_SMTP_USE_SSL", True)
        self.use_tls = _env_bool("NEUOPS_SMTP_USE_TLS", False)
        self.timeout = env_int("NEUOPS_SMTP_TIMEOUT", 15)
        self.enabled = _env_bool(
            "NEUOPS_MAIL_ENABLED", bool(self.user and self.password))

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.host and self.user
                    and self.password and self.from_addr)

    def send(self, to: list[str], subject: str, body: str) -> None:
        if not self.configured:
            raise MailError(
                "邮件服务未配置（需要 NEUOPS_SMTP_USER / "
                "NEUOPS_SMTP_PASSWORD）"
            )
        recipients = [addr.strip() for addr in to if addr and addr.strip()]
        if not recipients:
            raise MailError("没有收件人")
        message = EmailMessage()
        message["From"] = self.from_addr
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.set_content(body)
        try:
            if self.use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout,
                    context=context,
                ) as smtp:
                    smtp.login(self.user, self.password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(
                    self.host, self.port, timeout=self.timeout,
                ) as smtp:
                    smtp.ehlo()
                    if self.use_tls:
                        smtp.starttls(context=ssl.create_default_context())
                        smtp.ehlo()
                    smtp.login(self.user, self.password)
                    smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            logger.warning("邮件发送失败 to=%s: %s", recipients, exc)
            raise MailError(f"邮件发送失败: {exc}") from exc


def send_login_code(email: str, code: str, ttl_minutes: int) -> None:
    body = (
        "你正在登录 NeuOps。\n\n"
        f"邮箱验证码：{code}\n"
        f"有效期：{ttl_minutes} 分钟。\n\n"
        "如果不是你本人操作，请忽略此邮件并检查账号安全。"
    )
    Mailer().send([email], "NeuOps 登录验证码", body)


def _public_url() -> str:
    return env_text("NEUOPS_PUBLIC_URL").rstrip("/")


def _command_ticket_message(ticket: dict) -> tuple[str, str]:
    title = (ticket.get("title") or ticket.get("id") or "命令工单")[:120]
    subject = f"[NeuOps] 新命令工单待审批：{title}"
    lines = [
        "NeuOps 收到一张新的命令工单，等待管理员审批。",
        "",
        f"工单 ID：{ticket.get('id', '')}",
        f"提交人：{ticket.get('created_by', '')}",
        f"目标节点：{ticket.get('target_node_name', '')} "
        f"({ticket.get('target_node_address', '')})",
        f"风险等级：{ticket.get('risk_level', 'unknown')}",
        "",
        "需求描述：",
        (ticket.get("request") or "（无）")[:4000],
        "",
        "建议命令：",
        (ticket.get("command") or "（尚未填写）")[:8000],
    ]
    url = _public_url()
    if url:
        lines += ["", f"审批地址：{url}/ops/{ticket.get('id', '')}"]
    else:
        lines += ["", f"请在 NeuOps「运维工单」中打开：{ticket.get('id', '')}"]
    lines += ["", "请登录 NeuOps 审批；批准后命令仍需单独执行，单次授权只能执行一次。"]
    return subject, "\n".join(lines)


def _send_admin_notification(subject: str, body: str,
                             recipients: list[str]) -> None:
    try:
        Mailer().send(recipients, subject, body)
        logger.info("新工单通知已发送: %s", recipients)
    except MailError as exc:
        logger.warning("新工单通知发送失败: %s", exc)
    except Exception:
        logger.exception("新工单通知发送异常")


def notify_admins_new_command_ticket(ticket: dict) -> None:
    """命令工单进入 pending 后通知所有启用的管理员邮箱。"""
    if ticket.get("kind") != "command" or ticket.get("status") != "pending":
        return
    recipients = Database.get_instance().list_admin_emails()
    if not recipients:
        logger.warning("新命令工单 %s 待审批，但没有配置管理员邮箱",
                       ticket.get("id"))
        return
    subject, body = _command_ticket_message(ticket)
    if env_text("NEUOPS_MAIL_SYNC", "0").lower() in {"1", "true", "yes"}:
        _send_admin_notification(subject, body, recipients)
        return
    threading.Thread(
        target=_send_admin_notification,
        args=(subject, body, recipients),
        daemon=True,
        name="neuops-mail",
    ).start()
