"""Rroot 纯函数安全测试：命令包装、输出解析、脱敏、执行环境校验。"""

from __future__ import annotations

import base64
import re

import pytest

from neuops.master.services.rroot.security import (
    ValidationError,
    build_wrapped_command,
    content_hash,
    normalize_exec_env,
    parse_wrapped_output,
    redact_secrets,
    validate_command,
)


def test_wrapped_command_encodes_original_and_uses_marker():
    command = "echo 'hello' && printf 'world\\n'"
    wrapped = build_wrapped_command(
        command, "__TEST_MARKER__", 30,
        workdir="/tmp", env={"FOO": "a b", "BAR": "x'y"},
    )
    encoded = base64.b64encode(command.encode()).decode()
    assert encoded in wrapped
    assert "base64 -d" in wrapped
    assert "__TEST_MARKER__" in wrapped
    assert "timeout --signal=TERM" in wrapped
    assert "cd /tmp" in wrapped
    assert "export FOO='a b'" in wrapped
    assert "export BAR='x'\"'\"'y'" in wrapped


def test_parse_wrapped_output_separates_streams():
    marker = "__TEST_MARKER__"
    log = (
        "bash: cannot set terminal process group\n"
        f"{marker}_STDOUT_BEGIN\nout line\n{marker}_STDOUT_END\n"
        f"{marker}_STDERR_BEGIN\nerr line\n{marker}_STDERR_END\n"
    )
    parsed = parse_wrapped_output(log, marker)
    assert parsed["stdout"] == "out line"
    assert parsed["stderr"] == "err line"
    assert parsed["markers_found"] is True
    assert parsed["raw_log"] == log


def test_parse_wrapped_output_without_markers_keeps_raw_log():
    parsed = parse_wrapped_output("plain output", "__NOPE__")
    assert parsed["stdout"] == ""
    assert parsed["stderr"] == ""
    assert parsed["markers_found"] is False


def test_exec_env_fixed_root_and_validation():
    env = normalize_exec_env({"workdir": "/tmp", "timeout_seconds": 60})
    assert env["user"] == "root"
    assert env["workdir"] == "/tmp"
    with pytest.raises(ValidationError):
        normalize_exec_env({"user": "alice"})
    with pytest.raises(ValidationError):
        normalize_exec_env({"workdir": "relative"})
    with pytest.raises(ValidationError):
        normalize_exec_env({"env": {"bad-name": "x"}})


def test_command_validation_rejects_empty_and_nul():
    with pytest.raises(ValidationError):
        validate_command("   ")
    assert "\x00" not in validate_command("echo a\x00b")


def test_redact_secrets_handles_password_and_bearer():
    text = "password=abc123 token: xyz Authorization: Bearer sk-secret"
    redacted = redact_secrets(text)
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "sk-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_content_hash_is_stable():
    assert content_hash("a", "b") == content_hash("a", "b")
    assert content_hash("a", "b") != content_hash("a", "c")


def test_wrapped_command_executes_with_workdir_env_and_exit_code():
    import subprocess

    marker = "__LOCAL_TEST__"
    wrapped = build_wrapped_command(
        'echo out; echo err >&2; pwd; echo "$FOO"; exit 7',
        marker, 5, workdir="/tmp", env={"FOO": "bar"},
    )
    result = subprocess.run(
        ["bash", "-c", wrapped], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 7
    parsed = parse_wrapped_output(result.stdout, marker)
    assert "out" in parsed["stdout"]
    assert "/tmp" in parsed["stdout"]
    assert "bar" in parsed["stdout"]
    assert "err" in parsed["stderr"]
    assert parsed["markers_found"] is True
