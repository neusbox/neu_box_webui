#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SOURCE_DIR="${1:-/var/lib/neu-box/master}"
RUNTIME_DIR="$PROJECT_DIR/runtime"
DATA_DIR="$RUNTIME_DIR/data/master"
BACKUP_DIR="$RUNTIME_DIR/data/backups"
CONFIG_PATH="$RUNTIME_DIR/config/webui.env"
SOURCE_DB="$SOURCE_DIR/master.db"
ACTIVE_DB="$DATA_DIR/master.db"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGED_DB="$DATA_DIR/.master-from-var-$STAMP.db"
CURRENT_BACKUP="$BACKUP_DIR/master-before-var-$STAMP.db"
RAW_BACKUP="$BACKUP_DIR/master-before-var-$STAMP.raw.db"
FILES_BACKUP="$BACKUP_DIR/files-before-var-$STAMP"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

die() {
    echo "error: $*" >&2
    exit 1
}

command -v sqlite3 >/dev/null 2>&1 || die "未找到 sqlite3"
[[ -r "$SOURCE_DB" ]] || die "旧数据库不可读: $SOURCE_DB"
[[ -f "$CONFIG_PATH" ]] || die "本地配置不存在: $CONFIG_PATH；请先执行 ./run.sh --prepare-only"
[[ -x "$PYTHON_BIN" ]] || die "Python 环境不存在: $PYTHON_BIN；请先执行 ./run.sh --prepare-only"

if pgrep -af 'neu_box_webui\.master\.app.*serve' >/dev/null 2>&1; then
    die "检测到 WebUI 仍在运行，请先停止服务或在启动终端按 Ctrl-C"
fi

mkdir -p \
    "$DATA_DIR/uploads" \
    "$DATA_DIR/experiment-logs" \
    "$BACKUP_DIR" \
    "$RUNTIME_DIR/logs"

SOURCE_INTEGRITY="$(sqlite3 "$SOURCE_DB" 'PRAGMA integrity_check;')"
[[ "$SOURCE_INTEGRITY" == "ok" ]] || die "旧数据库完整性检查失败: $SOURCE_INTEGRITY"

table_count() {
    local database="$1"
    local table="$2"
    local exists
    exists="$(sqlite3 "$database" \
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='$table';")"
    if [[ "$exists" == "1" ]]; then
        sqlite3 "$database" "SELECT count(*) FROM $table;"
    else
        printf '%s' "n/a"
    fi
}

echo "[migrate] 旧数据库完整性: ok"
echo "[migrate] 旧数据库用户数: $(table_count "$SOURCE_DB" users)"
echo "[migrate] 旧数据库实验数: $(table_count "$SOURCE_DB" experiments)"
echo "[migrate] 旧数据库目录数: $(table_count "$SOURCE_DB" folders)"

if [[ -f "$ACTIVE_DB" ]]; then
    sqlite3 "$ACTIVE_DB" ".backup '$CURRENT_BACKUP'"
    [[ "$(sqlite3 "$CURRENT_BACKUP" 'PRAGMA integrity_check;')" == "ok" ]] || \
        die "当前数据库备份校验失败"
    echo "[migrate] 已备份当前数据库: $CURRENT_BACKUP"
fi

sqlite3 "$SOURCE_DB" ".backup '$STAGED_DB'"
[[ "$(sqlite3 "$STAGED_DB" 'PRAGMA integrity_check;')" == "ok" ]] || \
    die "暂存数据库校验失败"

mkdir -p "$FILES_BACKUP"
if [[ -d "$DATA_DIR/uploads" ]]; then
    cp -R "$DATA_DIR/uploads" "$FILES_BACKUP/"
fi
if [[ -d "$DATA_DIR/experiment-logs" ]]; then
    cp -R "$DATA_DIR/experiment-logs" "$FILES_BACKUP/"
fi

if [[ -f "$ACTIVE_DB" ]]; then
    mv "$ACTIVE_DB" "$RAW_BACKUP"
fi
for suffix in -wal -shm; do
    if [[ -e "${ACTIVE_DB}${suffix}" ]]; then
        mv "${ACTIVE_DB}${suffix}" "${RAW_BACKUP}${suffix}"
    fi
done
mv "$STAGED_DB" "$ACTIVE_DB"

if [[ -d "$SOURCE_DIR/experiment-logs" ]]; then
    cp -R "$SOURCE_DIR/experiment-logs/." "$DATA_DIR/experiment-logs/"
fi
if [[ -d "$SOURCE_DIR/uploads" ]]; then
    cp -R "$SOURCE_DIR/uploads/." "$DATA_DIR/uploads/"
fi

env -u NEU_BOX_DB_PATH -u db_dir \
    "$PYTHON_BIN" -m neu_box_webui.master.app \
    --config "$CONFIG_PATH" db migrate
env -u ADMIN_USER -u ADMIN_PASS -u NEU_BOX_DB_PATH -u db_dir \
    "$PYTHON_BIN" -m neu_box_webui.master.app \
    --config "$CONFIG_PATH" admin reset-password --from-config

FINAL_INTEGRITY="$(sqlite3 "$ACTIVE_DB" 'PRAGMA integrity_check;')"
[[ "$FINAL_INTEGRITY" == "ok" ]] || die "迁移后数据库完整性检查失败: $FINAL_INTEGRITY"

echo "[migrate] 迁移后数据库完整性: ok"
echo "[migrate] 迁移后用户数: $(table_count "$ACTIVE_DB" users)"
echo "[migrate] 迁移后实验数: $(table_count "$ACTIVE_DB" experiments)"
echo "[migrate] 迁移后目录数: $(table_count "$ACTIVE_DB" folders)"
echo "[migrate] 文件备份: $FILES_BACKUP"
echo "[migrate] 原始数据未删除: $SOURCE_DIR"
echo "[migrate] 完成；现在可以执行 ./run.sh 启动 WebUI"
