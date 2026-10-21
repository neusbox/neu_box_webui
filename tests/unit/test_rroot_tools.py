"""只读工具命令构造：白名单、参数引用与路径限制。"""

from __future__ import annotations

import pytest

from neuops.master.services.rroot.security import ValidationError
from neuops.master.services.rroot.tools import build_tool_command


def test_ls_path_is_shell_quoted():
    command, cwd = build_tool_command(
        "ls", {"path": "/tmp; rm -rf /"}, "/root")
    assert cwd == "/root"
    assert "ls -la -- '/tmp; rm -rf /'" in command
    assert "cd /root" in command


def test_tail_lines_are_bounded():
    command, _ = build_tool_command(
        "tail", {"path": "/var/log/syslog", "lines": 9999}, "/")
    assert "tail -n 500 -- /var/log/syslog" in command


def test_cd_only_changes_virtual_cwd():
    command, cwd = build_tool_command("cd", {"path": "log"}, "/var")
    assert command == ""
    assert cwd == "/var/log"


def test_unknown_tool_and_sensitive_path_are_rejected():
    with pytest.raises(ValidationError):
        build_tool_command("bash", {"command": "id"}, "/")
    with pytest.raises(ValidationError):
        build_tool_command("cat", {"path": "/etc/shadow"}, "/")
    with pytest.raises(ValidationError):
        build_tool_command("cat", {"path": "/root/.ssh/id_rsa"}, "/")
