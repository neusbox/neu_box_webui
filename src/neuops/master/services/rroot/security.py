"""Rroot 安全边界：输入校验、审批快照、提示词注入防护与命令包装。

Rroot（Restricted Root）是 NeuOps 中唯一允许向 worker 提交 ``user_id=root``
命令的模块。它没有对外的通用执行接口：只有管理员在工单上完成审批后，
执行器才会消费一次性的审批快照。

本模块刻意不 import 任何 AI 客户端或普通任务 API，避免特权路径被
AI/普通用户间接复用。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import shlex
import time
from pathlib import Path
from typing import Any


TICKET_KINDS = frozenset({"command", "consult", "knowledge"})
TICKET_STATUSES = frozenset({
    "draft", "pending", "approved", "executing", "succeeded", "failed",
    "rejected", "cancelled",
})
KNOWLEDGE_KINDS = frozenset({"experience", "runbook", "constraint", "faq"})
RISK_LEVELS = frozenset({"unknown", "low", "medium", "high", "critical"})

ROOT_USER = "root"
DEFAULT_TIMEOUT_SECONDS = 3600
MAX_COMMAND_BYTES = 20_000
MAX_TEXT_BYTES = 20_000
MAX_REQUEST_CHARS = 1_000
MAX_ENV_KEYS = 64
MAX_ENV_VALUE = 4096

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|"
        r"authorization)\b(\s*[:=]\s*)(?:Bearer\s+)?([^\s,;\"']+)"
    ),
    re.compile(
        r"(?i)(--?(?:password|token|secret|api[_-]?key)\s+)([^\s,;\"']+)"
    ),
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.S,
)
_TOKEN_RE = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{10,}|"
    r"github_pat_[A-Za-z0-9_]{10,}|AKIA[0-9A-Z]{12,})\b"
)
_INJECTION_RE = re.compile(
    r"(?i)("
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?|"
    r"disregard\s+(all\s+)?(previous|prior|above)|"
    r"system\s+prompt|developer\s+message|"
    r"忽略(之前|以上|前面|所有)(的)?(指令|提示|消息)|"
    r"忽略之前|无视之前|你现在是|从现在开始你|"
    r"不要告诉|泄露.*(密钥|密码|token)|"
    r"</?untrusted|</?system|</?assistant"
    r")"
)

RISK_PATTERNS = (
    (re.compile(r"(?i)\brm\s+(-[a-z]*[rf][a-z]*\s+)+/(\s|$)"), "critical"),
    (re.compile(r"(?i)\b(mkfs(\.\w+)?|wipefs|fdisk|parted)\b"), "critical"),
    (re.compile(r"(?i)\bdd\b.*\bof=/dev/"), "critical"),
    (re.compile(r"(?i)\b(shutdown|reboot|poweroff|halt|init\s+0)\b"), "critical"),
    (re.compile(r"(?i)\b(chmod|chown)\s+-R\s+.*\s+/(\s|$)"), "high"),
    (re.compile(r"(?i)\b(curl|wget)\b[^|]*\|\s*(ba)?sh\b"), "high"),
    (re.compile(r"(?i)\b(iptables|nft|ufw|firewall-cmd)\b"), "high"),
    (re.compile(r"(?i)\b(systemctl|service)\s+(stop|disable|mask)\b"), "high"),
    (re.compile(r"(?i)\b(userdel|groupdel|passwd|visudo|crontab)\b"), "high"),
    (re.compile(r"(?i)/etc/(passwd|shadow|sudoers)"), "high"),
    (re.compile(r"(?i)\b(kill|pkill|killall)\s+-9\b"), "medium"),
    (re.compile(r"(?i)\b(docker|kubectl|podman)\b"), "medium"),
)


class ValidationError(ValueError):
    """用户输入或审批内容不合法。"""


def now() -> float:
    return time.time()


def new_id(prefix: str = "") -> str:
    value = secrets.token_hex(8)
    return f"{prefix}{value}" if prefix else value


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def content_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\x00")
    return digest.hexdigest()


def json_loads(value: str | None, default: Any) -> Any:
    """解析 JSON 字符串；已经是目标类型时原样返回。"""
    if not value:
        return default
    if isinstance(value, type(default)):
        return value
    if not isinstance(value, str):
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def clean_text(value: Any, *, max_bytes: int = MAX_TEXT_BYTES,
               max_chars: int | None = None,
               field: str = "文本", allow_empty: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field}必须是字符串")
    value = value.replace("\x00", "").strip()
    if not allow_empty and not value:
        raise ValidationError(f"{field}不能为空")
    if max_chars is not None and len(value) > max_chars:
        raise ValidationError(f"{field}过长（最多 {max_chars} 字）")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValidationError(f"{field}过长（最多 {max_bytes} 字节）")
    return value


def validate_command(value: Any) -> str:
    return clean_text(
        value, max_bytes=MAX_COMMAND_BYTES, field="命令", allow_empty=False,
    )


def classify_risk(command: str) -> str:
    for pattern, level in RISK_PATTERNS:
        if pattern.search(command):
            return level
    return "low" if command.strip() else "unknown"


def normalize_params(raw: Any) -> dict:
    if raw in (None, {}):
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError("执行参数必须是对象")
    unknown = set(raw) - {"cpu", "memory", "mem_unit", "device_num", "priority", "est_time"}
    if unknown:
        raise ValidationError(f"不支持的执行参数: {', '.join(sorted(unknown))}")

    def _int(name: str, default: int, low: int, high: int) -> int:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"{name} 必须是整数")
        if not low <= value <= high:
            raise ValidationError(f"{name} 必须在 {low}-{high} 之间")
        return value

    mem_unit = str(raw.get("mem_unit", "GB")).upper()
    if mem_unit not in {"GB", "MB"}:
        raise ValidationError("mem_unit 必须是 GB 或 MB")
    return {
        "cpu": _int("cpu", 0, 0, 4096),
        "memory": _int("memory", 0, 0, 1_048_576),
        "mem_unit": mem_unit,
        "device_num": _int("device_num", 0, 0, 0),
        "priority": _int("priority", 0, 0, 1),
        "est_time": _int("est_time", 0, 0, 525_600),
    }


def normalize_exec_env(raw: Any) -> dict:
    """固定 root 身份，只允许管理员调整工作目录、环境变量和超时。"""
    if raw in (None, {}):
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError("执行环境必须是对象")
    unknown = set(raw) - {"user", "shell", "login", "workdir", "env", "timeout_seconds"}
    if unknown:
        raise ValidationError(f"不支持的执行环境字段: {', '.join(sorted(unknown))}")

    user = str(raw.get("user") or ROOT_USER)
    if user != ROOT_USER:
        raise ValidationError("Rroot 执行环境固定为 root")

    shell = str(raw.get("shell") or "bash")
    if shell not in {"bash", "/bin/bash"}:
        raise ValidationError("Rroot 只允许 bash")

    login = raw.get("login", True)
    if not isinstance(login, bool):
        raise ValidationError("login 必须是布尔值")
    if not login:
        raise ValidationError("Rroot 固定使用登录 shell（login=true）")

    workdir = str(raw.get("workdir") or f"/root")
    if not workdir.startswith("/") or "\x00" in workdir:
        raise ValidationError("workdir 必须是绝对路径")
    workdir = str(Path(workdir))
    if len(workdir) > 4096:
        raise ValidationError("workdir 过长")

    timeout = raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ValidationError("timeout_seconds 必须是整数")
    if not 1 <= timeout <= 86_400:
        raise ValidationError("timeout_seconds 必须在 1-86400 秒之间")

    env = raw.get("env") or {}
    if not isinstance(env, dict):
        raise ValidationError("env 必须是对象")
    if len(env) > MAX_ENV_KEYS:
        raise ValidationError(f"env 最多 {MAX_ENV_KEYS} 项")
    normalized_env: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_NAME_RE.fullmatch(key):
            raise ValidationError(f"非法环境变量名: {key!r}")
        if not isinstance(value, (str, int, float, bool)):
            raise ValidationError(f"环境变量 {key} 必须是标量")
        text = str(value)
        if "\x00" in text or len(text) > MAX_ENV_VALUE:
            raise ValidationError(f"环境变量 {key} 的值非法或过长")
        normalized_env[key] = text

    return {
        "user": ROOT_USER,
        "shell": "bash",
        "login": login,
        "workdir": workdir,
        "env": normalized_env,
        "timeout_seconds": timeout,
    }


def redact_secrets(text: str, limit: int = 12_000) -> str:
    """给 AI 上下文使用的脱敏副本；落盘日志不做修改。"""
    if not text:
        return ""
    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _TOKEN_RE.sub("[REDACTED TOKEN]", text)
    for pattern in _SECRET_PATTERNS:
        def _replace(match: re.Match) -> str:
            if (match.lastindex or 0) >= 3:
                return f"{match.group(1)}{match.group(2)}[REDACTED]"
            return f"{match.group(1)}[REDACTED]"
        text = pattern.sub(_replace, text)
    return text[:limit]


def looks_like_prompt_injection(text: str) -> bool:
    return bool(text and _INJECTION_RE.search(text))


def untrusted_block(label: str, value: str, nonce: str,
                    limit: int = 12_000) -> str:
    """把历史工单/日志包装成模型必须当作数据而非指令的区块。"""
    safe_label = re.sub(r"[^A-Za-z0-9_.:-]", "_", label)[:64]
    body = (value or "")[:limit].replace(
        "</UNTRUSTED", "&lt;/UNTRUSTED"
    ).replace(
        "<UNTRUSTED", "&lt;UNTRUSTED"
    )
    return (
        f'<UNTRUSTED_DATA_{nonce} source="{safe_label}">\n'
        f"{body}\n"
        f"</UNTRUSTED_DATA_{nonce}>"
    )


def trusted_knowledge_block(label: str, value: str, nonce: str,
                            limit: int = 12_000) -> str:
    """管理员已批准的知识库内容；模型应作为权威事实直接采用。"""
    safe_label = re.sub(r"[^A-Za-z0-9_.:-]", "_", label)[:64]
    body = (value or "")[:limit]
    return (
        f'<TRUSTED_KNOWLEDGE_{nonce} source="{safe_label}">\n'
        f"{body}\n"
        f"</TRUSTED_KNOWLEDGE_{nonce}>"
    )


def build_approval_snapshot(ticket: dict, actor: str) -> tuple[dict, str, str]:
    """返回 (snapshot, hash, one_time_token)。批准后写入不可变列。"""
    snapshot = {
        "ticket_id": ticket["id"],
        "kind": ticket["kind"],
        "command": ticket["command"],
        "params": json_loads(ticket.get("params"), {}),
        "target": {
            "node_id": ticket["target_node_id"],
            "node_name": ticket["target_node_name"],
            "address": ticket["target_node_address"],
        },
        "exec_env": json_loads(ticket.get("exec_env"), {}),
        "approved_by": actor,
        "approved_at": now(),
        "nonce": secrets.token_hex(16),
    }
    digest = hashlib.sha256(
        canonical_json(snapshot).encode("utf-8")
    ).hexdigest()
    return snapshot, digest, secrets.token_urlsafe(32)


def verify_snapshot(ticket: dict) -> dict:
    snapshot = json_loads(ticket.get("approval_snapshot"), {})
    if not snapshot or not ticket.get("approval_hash"):
        raise ValidationError("工单缺少审批快照")
    digest = hashlib.sha256(
        canonical_json(snapshot).encode("utf-8")
    ).hexdigest()
    if digest != ticket["approval_hash"]:
        raise ValidationError("审批快照校验失败")
    if snapshot.get("command") != ticket.get("command"):
        raise ValidationError("命令与审批快照不一致")
    if snapshot.get("target", {}).get("node_id") != ticket.get("target_node_id"):
        raise ValidationError("目标节点与审批快照不一致")
    if snapshot.get("target", {}).get("address") != ticket.get("target_node_address"):
        raise ValidationError("目标节点地址与审批快照不一致")
    if snapshot.get("params") != json_loads(ticket.get("params"), {}):
        raise ValidationError("执行参数与审批快照不一致")
    if snapshot.get("exec_env") != json_loads(ticket.get("exec_env"), {}):
        raise ValidationError("执行环境与审批快照不一致")
    return snapshot


def build_wrapped_command(command: str, marker: str, timeout: int,
                          workdir: str = "/root",
                          env: dict[str, str] | None = None) -> str:
    """把命令包成可分离 stdout/stderr、可超时且带执行环境的一次性脚本。

    worker 的日志把 stdout/stderr 合并；这里用临时文件分离，并用随机
    marker 包围，执行器据此还原两路输出。命令本身通过 base64 传递，
    避免引号/换行导致的二次解析问题。workdir/env 由审批快照提供，
    在这里显式应用，确保实际执行环境与快照一致。
    """
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    exports = [
        f"export {key}={shlex.quote(str(value))}"
        for key, value in (env or {}).items()
    ]
    env_block = ("  " + "\n  ".join(exports) + "\n") if exports else ""
    return f"""__neuops_marker='{marker}'
__neuops_out=$(mktemp "${{TMPDIR:-/tmp}}/neuops-out.XXXXXX") || exit 125
__neuops_err=$(mktemp "${{TMPDIR:-/tmp}}/neuops-err.XXXXXX") || exit 125
__neuops_cmd=$(printf '%s' '{encoded}' | base64 -d) || exit 125
(
  cd {shlex.quote(workdir or "/root")} || exit 126
{env_block}  if command -v timeout >/dev/null 2>&1; then
    timeout --signal=TERM --kill-after=10 {int(timeout)} bash -c "$__neuops_cmd"
  else
    bash -c "$__neuops_cmd"
  fi
) >"$__neuops_out" 2>"$__neuops_err"
__neuops_rc=$?
printf '\\n%s_STDOUT_BEGIN\\n' "$__neuops_marker"
cat "$__neuops_out"
printf '\\n%s_STDOUT_END\\n' "$__neuops_marker"
printf '\\n%s_STDERR_BEGIN\\n' "$__neuops_marker"
cat "$__neuops_err"
printf '\\n%s_STDERR_END\\n' "$__neuops_marker"
rm -f "$__neuops_out" "$__neuops_err"
exit "$__neuops_rc"
"""


def parse_wrapped_output(log_text: str, marker: str) -> dict:
    """从 worker 合并日志中还原 stdout/stderr。"""
    stdout = stderr = ""
    found_out = found_err = False
    out_begin = f"{marker}_STDOUT_BEGIN"
    out_end = f"{marker}_STDOUT_END"
    err_begin = f"{marker}_STDERR_BEGIN"
    err_end = f"{marker}_STDERR_END"

    def _extract(start: str, end: str) -> tuple[str, bool]:
        i = log_text.find(start)
        if i < 0:
            return "", False
        i += len(start)
        if i < len(log_text) and log_text[i] == "\n":
            i += 1
        j = log_text.find(end, i)
        if j < 0:
            return log_text[i:], False
        value = log_text[i:j]
        if value.endswith("\n"):
            value = value[:-1]
        return value, True

    if out_begin in log_text:
        stdout, found_out = _extract(out_begin, out_end)
    if err_begin in log_text:
        stderr, found_err = _extract(err_begin, err_end)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "markers_found": found_out and found_err,
        "raw_log": log_text,
    }


def public_ticket(ticket: dict, *, include_secret: bool = False) -> dict:
    """输出给前端的工单视图；永不返回 approval_token。"""
    result = dict(ticket)
    result.pop("approval_token", None)
    if not include_secret:
        result.pop("approval_hash", None)
    for key in ("exec_env", "params", "ai_suggestion", "knowledge_payload",
                "approval_snapshot", "exec_result"):
        if key in result:
            result[key] = json_loads(result.get(key), {})
    return result
