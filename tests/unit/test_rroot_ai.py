"""AI 统一使用 Chat Completions + 前缀续写生成 JSON。"""

from __future__ import annotations

import json

from neuops.master.services.rroot.ai import AIClient


class _Resp:
    def __init__(self, payload, status_code=200, text=""):
        self.ok = 200 <= status_code < 300
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


def test_chat_prefix_completion_forces_json_prefix(monkeypatch):
    monkeypatch.setenv("NEUOPS_AI_ENABLED", "1")
    monkeypatch.setenv("NEUOPS_AI_API_KEY", "test-key")
    monkeypatch.setenv("NEUOPS_AI_MODEL", "test-model")
    monkeypatch.setenv("NEUOPS_AI_BASE_URL", "https://api.deepseek.com/beta")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Resp({
            "choices": [{
                "message": {"content": '"reply":"ok","command":"df -h /var"}'},
            }],
        })

    import neuops.master.services.rroot.ai as ai_module
    monkeypatch.setattr(ai_module.requests, "post", fake_post)

    out = AIClient().complete_json("SYSTEM", "USER")
    assert out.startswith("{")
    assert json.loads(out)["command"] == "df -h /var"
    assert captured["url"] == "https://api.deepseek.com/beta/chat/completions"
    assert captured["payload"]["messages"][-1] == {
        "role": "assistant", "content": "{", "prefix": True,
    }
    assert "response_format" not in captured["payload"]


def test_prefix_unsupported_falls_back_to_plain_chat_json(monkeypatch):
    monkeypatch.setenv("NEUOPS_AI_ENABLED", "1")
    monkeypatch.setenv("NEUOPS_AI_API_KEY", "test-key")
    monkeypatch.setenv("NEUOPS_AI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("NEUOPS_AI_BASE_URL", "https://api.openai.com/v1")

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        if len(calls) == 1:
            return _Resp({"error": "unknown prefix"}, status_code=400,
                         text="unknown prefix")
        if len(calls) == 2:
            return _Resp({"error": "bad prefill"}, status_code=400,
                         text="bad prefill")
        return _Resp({
            "choices": [{"message": {"content": '{"reply":"ok","command":"id"}'}}],
        })

    import neuops.master.services.rroot.ai as ai_module
    monkeypatch.setattr(ai_module.requests, "post", fake_post)

    out = AIClient().complete_json("SYSTEM", "USER")
    assert out.startswith("{")
    assert json.loads(out)["command"] == "id"
    assert calls[0]["messages"][-1]["prefix"] is True
    assert "prefix" not in calls[1]["messages"][-1]
    assert calls[2]["response_format"] == {"type": "json_object"}
    assert calls[2]["messages"][-1]["role"] == "user"


def test_approved_knowledge_is_marked_trusted():
    from neuops.master.services.rroot.ai import _context_block

    block = _context_block(
        "nonce",
        [],
        [{
            "id": "k1", "title": "建用户", "content": "useradd -m pengyt",
            "kind": "runbook", "tags": ["user"],
        }],
        None,
    )
    assert "TRUSTED_KNOWLEDGE" in block
    assert "useradd -m pengyt" in block
    assert "UNTRUSTED_DATA" not in block


def test_empty_json_object_falls_through_to_next_attempt(monkeypatch):
    monkeypatch.setenv("NEUOPS_AI_ENABLED", "1")
    monkeypatch.setenv("NEUOPS_AI_API_KEY", "test-key")
    monkeypatch.setenv("NEUOPS_AI_MODEL", "test-model")

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        if len(calls) < 3:
            return _Resp({
                "choices": [{"message": {"content": "{}"}}],
            })
        return _Resp({
            "choices": [{
                "message": {"content": '{"reply":"ok","command":"id"}'},
            }],
        })

    import neuops.master.services.rroot.ai as ai_module
    monkeypatch.setattr(ai_module.requests, "post", fake_post)

    out = AIClient().complete_json("SYSTEM", "USER")
    assert json.loads(out)["command"] == "id"
    assert len(calls) == 3
    assert calls[2].get("response_format") == {"type": "json_object"}
