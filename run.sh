#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CALLER_DIR="$(pwd -P)"
CONFIG_PATH="${NEU_BOX_WEBUI_CONFIG:-$SCRIPT_DIR/.env}"
PREPARE_ONLY=0
SERVE_ARGS=()

usage() {
    cat <<'EOF'
用法: ./run.sh [选项] [-- serve 参数]

同步锁定依赖、初始化配置和节点列表、迁移数据库，然后直接启动 WebUI。

选项:
  --config PATH   使用指定配置文件（默认: 项目根目录/.env）
  --prepare-only  只完成初始化和数据库迁移，不启动服务
  -h, --help      显示帮助

示例:
  ./run.sh
  ./run.sh -- --listen 127.0.0.1 --port 25565
  ./run.sh --config /etc/neu-box/webui.env
EOF
}

while (($#)); do
    case "$1" in
        --config)
            if (($# < 2)); then
                echo "error: --config 需要路径" >&2
                exit 2
            fi
            CONFIG_PATH="$2"
            shift 2
            ;;
        --prepare-only)
            PREPARE_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            SERVE_ARGS=("$@")
            break
            ;;
        *)
            SERVE_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ "$CONFIG_PATH" != /* ]]; then
    CONFIG_PATH="$CALLER_DIR/$CONFIG_PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "error: 未找到 uv，请先安装: https://docs.astral.sh/uv/" >&2
    exit 1
fi

cd "$SCRIPT_DIR"
unset VIRTUAL_ENV || true

echo "[webui] 同步运行依赖"
uv sync --frozen --no-dev
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"

CREATED_CONFIG=0
GENERATED_ADMIN_PASS=""
if [[ ! -e "$CONFIG_PATH" ]]; then
    mkdir -p "$(dirname -- "$CONFIG_PATH")"
    cp "$SCRIPT_DIR/deploy/config/master.env.example" "$CONFIG_PATH"
    chmod 600 "$CONFIG_PATH"
    CREATED_CONFIG=1
    echo "[webui] 已创建配置: $CONFIG_PATH"
elif [[ ! -f "$CONFIG_PATH" ]]; then
    echo "error: 配置路径不是普通文件: $CONFIG_PATH" >&2
    exit 1
fi

replace_setting() {
    local key="$1"
    local value="$2"
    local temporary
    temporary="$(mktemp "${CONFIG_PATH}.tmp.XXXXXX")"
    sed "s|^${key}=.*$|${key}=${value}|" "$CONFIG_PATH" >"$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$CONFIG_PATH"
}

if grep -qx 'SECRET_KEY=' "$CONFIG_PATH"; then
    replace_setting \
        SECRET_KEY \
        "$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(32))')"
    echo "[webui] 已生成并保存 SECRET_KEY"
fi

if ((CREATED_CONFIG)) && grep -qx 'ADMIN_PASS=admin' "$CONFIG_PATH"; then
    GENERATED_ADMIN_PASS="$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(18))')"
    replace_setting ADMIN_PASS "$GENERATED_ADMIN_PASS"
elif grep -qx 'ADMIN_PASS=admin' "$CONFIG_PATH"; then
    echo "[webui] 警告: 当前 ADMIN_PASS 仍为默认值 admin，请立即修改" >&2
fi

NODES_PATH="$($PYTHON_BIN - "$CONFIG_PATH" <<'PY'
import sys

from neu_box_webui.config import load_role_environment
from neu_box_webui.master.paths import nodes_config_path

load_role_environment("webui", sys.argv[1])
print(nodes_config_path())
PY
)"
if [[ ! -e "$NODES_PATH" ]]; then
    mkdir -p "$(dirname -- "$NODES_PATH")"
    cp "$SCRIPT_DIR/deploy/config/nodes.json.example" "$NODES_PATH"
    echo "[webui] 已创建节点配置: $NODES_PATH"
elif [[ ! -f "$NODES_PATH" ]]; then
    echo "error: 节点配置路径不是普通文件: $NODES_PATH" >&2
    exit 1
fi

echo "[webui] 执行数据库迁移"
"$PYTHON_BIN" -m neu_box_webui.master.app \
    --config "$CONFIG_PATH" db migrate

if [[ -n "$GENERATED_ADMIN_PASS" ]]; then
    echo "[webui] 首次管理员账号: admin"
    echo "[webui] 首次管理员密码: $GENERATED_ADMIN_PASS"
    echo "[webui] 密码已写入 $CONFIG_PATH，请妥善保存"
fi

if ((PREPARE_ONLY)); then
    echo "[webui] 初始化完成"
    exit 0
fi

echo "[webui] 启动服务"
exec "$PYTHON_BIN" -m neu_box_webui.master.app \
    --config "$CONFIG_PATH" serve "${SERVE_ARGS[@]}"
