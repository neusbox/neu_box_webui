#!/usr/bin/env bash
# Neu Box WebUI 安装脚本（无安装器版本）。
#
# 用法（在解包后的目录内）:
#   sudo ./install.sh
#
# 行为:
#   1. 校验 root
#   2. 释放到 /opt/neu-box/webui/releases/<VERSION>，链接 current
#   3. 首次安装写 /etc/neu-box/webui.env（已存在则不动）
#   4. 安装 systemd unit（已存在则不动）并启用服务
#
# 从旧单体布局（/opt/neu-box/current/master）迁移: 见 README "从 0.2.x 迁移"。
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "error: 需要 root (sudo ./install.sh)" >&2
    exit 1
fi

STAGE="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(cat "$STAGE/VERSION")"
BASE=/opt/neu-box/webui
TARGET="$BASE/releases/$VERSION"
ETC=/etc/neu-box
ENV_FILE="$ETC/webui.env"
UNIT_SRC="$STAGE/systemd/neu-box-webui.service"
UNIT_DST="/etc/systemd/system/neu-box-webui.service"

# 版本钉死: 同一版本号内容不允许覆盖
if [ -e "$TARGET" ]; then
    echo "error: $TARGET 已存在；同一版本号不允许重复安装，请使用新版本" >&2
    exit 1
fi

mkdir -p "$BASE/releases" "$ETC" /var/lib/neu-box/master /var/log/neu-box
cp -a "$STAGE/master" "$TARGET/"
ln -sfn "$TARGET" "$BASE/current"

if [ -f "$ENV_FILE" ]; then
    echo "keep existing $ENV_FILE"
else
    install -m 0640 "$STAGE/config/master.env.example" "$ENV_FILE"
    echo "wrote $ENV_FILE（请检查 SECRET_KEY / ADMIN_USER / ADMIN_PASS）"
fi

if [ -f "$UNIT_DST" ]; then
    echo "keep existing $UNIT_DST"
else
    install -m 0644 "$UNIT_SRC" "$UNIT_DST"
fi

systemctl daemon-reload
systemctl enable neu-box-webui.service
systemctl restart neu-box-webui.service
systemctl --no-pager status neu-box-webui.service --lines=5 || true

if [ -x /opt/neu-box/current/master/neu-box-master ]; then
    echo
    echo "注意: 检测到旧单体布局 master（/opt/neu-box/current/master）。"
    echo "旧服务 neu-box-master.service 仍在运行；确认本服务正常后手动停用:"
    echo "  systemctl disable --now neu-box-master.service"
fi
