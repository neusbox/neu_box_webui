"""前端 api.js 冒烟测试（经 node 执行）+ 构建产物检查。

防止回归：JSON 请求的 Content-Type 曾写到 fetch init.headers 拷贝之前
的源对象上，导致浏览器请求不带 Content-Type，Flask 的
get_json(silent=True) 拿到 None —— 登录/注册/提交全部失败并误报
"用户名需 2-32 位字母、数字、_、. 或 -"（真实用户名根本没送到服务端）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
STATIC_DIR = WEB_DIR.parent / "src" / "neu_box_webui" / "master" / "static"


def test_api_js_smoke():
    """node 直接执行 web/test/api.test.mjs（fetch 打桩，断言请求头）。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 不可用")
    result = subprocess.run(
        [node, str(WEB_DIR / "test" / "api.test.mjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"api.js 冒烟测试失败:\n{result.stdout}\n{result.stderr}"
    )


def test_built_bundle_sends_content_type():
    """构建产物必须把 Content-Type 写到 fetch init 的 headers 上。"""
    bundles = sorted(STATIC_DIR.glob("assets/*.js"))
    if not bundles:
        pytest.skip("无构建产物（先 cd web && npm run build）")
    for bundle in bundles:
        text = bundle.read_text(encoding="utf-8")
        assert re.search(
            r'\.headers\["Content-Type"\]\s*=\s*"application/json"', text
        ), f"{bundle.name} 缺少修复后的 Content-Type 写法（需重新构建）"
