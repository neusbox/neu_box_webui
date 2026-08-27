# Neu Box WebUI (Master)

Neu Box 的中心节点：节点池管理、任务转发、实验记录、Web 界面。
2026-08-25 从 `neu_box` 单体仓库（0.2.2 线）拆分独立维护。

三仓库关系：

| 仓库 | 角色 | 版本 |
|---|---|---|
| [neu_box](https://github.com/neusbox/neu_box) | worker（节点侧设备沙盒）+ 聚合（e2e 测试、submodule 兼容矩阵） | 0.3.0+ |
| **neu_box_webui**（本仓库） | WebUI / master | 0.1.0+ |
| [neu_box_goClient](https://github.com/neusbox/neu_box_goClient) | Go 客户端 `neu-sbox`（直连 worker） | 0.2.0+ |

三者只通过 HTTP 契约相交，代码零依赖（共享的 `config` / `logging_config` /
`database` 迁移引擎为本仓库自有副本，与 neu_box 仓库同源、独立演进）。

## 与 worker 的兼容

- 本仓库 `API_VERSION = 2`（`src/neu_box_webui/__init__.py`）
- 心跳时读取 worker `/status` 的 `api_version`，低于本仓库要求时打 WARNING
- 兼容矩阵：

| WebUI | 最低 worker |
|---|---|
| 0.1.0 | 0.4.0（`/tasks`，`api_version = 2`） |

API 契约：[docs/master-api.md](docs/master-api.md)；
worker 侧契约见 neu_box 仓库 `docs/worker-api.md`。

## 直接运行

WebUI 不制作二进制或发布压缩包。目标机需要 Python 3.11+ 和
[uv](https://docs.astral.sh/uv/)，从源码同步依赖后直接启动 Python 模块。

```bash
./run.sh
```

`run.sh` 会同步锁定依赖、创建环境和节点配置、生成稳定的
`SECRET_KEY`、迁移数据库，然后启动 Python 服务。测试阶段默认
管理员账号为 `admin`，密码为 `231415926@qq.com`，公告中也会直接
显示该信息。当前用户系统尚未经过严格安全测试，请勿将服务直接
暴露到公网。

所有默认运行状态都收拢在项目的 `runtime/` 目录，首次运行自动创建：

```text
runtime/
├── config/webui.env
├── config/nodes.json
├── data/master/master.db
├── data/master/uploads/
├── data/master/experiment-logs/
├── data/backups/
└── logs/
```

`runtime/` 已加入 `.gitignore`，不会污染仓库。如果需要外置数据，仍可在
`runtime/config/webui.env` 中使用 `NEU_BOX_*` 路径变量逐项覆盖。

如果数据库中已有旧管理员、配置密码与数据库不一致，或管理员密码丢失，执行：

```bash
./run.sh --reset-admin-password
```

该命令会生成新的随机密码，同时更新 `runtime/config/webui.env`
与管理员密码哈希，然后退出而不启动
服务。服务已运行时不需要为了重置密码删除数据库。

只初始化而不启动，或覆盖监听地址：

```bash
./run.sh --prepare-only
./run.sh -- --listen 127.0.0.1 --port 25565
```

底层仍可直接执行 Python 模块：

```bash
uv run python -m neu_box_webui.master.app --config runtime/config/webui.env db migrate
uv run python -m neu_box_webui.master.app --config runtime/config/webui.env serve
```

## 测试

```bash
uv sync --frozen
uv run pytest
```

## 可选 systemd 托管

仓库提供 `deploy/systemd/neu-box-webui.service` 示例，它直接运行源码环境
中的 Python。示例假定仓库位于
`/opt/neu-box/webui`：

```bash
cd /opt/neu-box/webui
uv sync --frozen --no-dev
sudo install -d -o neu-box -g neu-box /opt/neu-box/webui/runtime
sudo -u neu-box ./run.sh --prepare-only

# 需要时编辑项目内的配置
sudoedit /opt/neu-box/webui/runtime/config/webui.env
sudo install -m 0644 deploy/systemd/neu-box-webui.service \
  /etc/systemd/system/neu-box-webui.service
sudo systemctl daemon-reload
sudo systemctl enable --now neu-box-webui.service
```

更新时只需拉取代码、同步锁定依赖、迁移数据库并重启服务：

```bash
git pull --ff-only
uv sync --frozen --no-dev
sudo -u neu-box .venv/bin/python -m neu_box_webui.master.app \
  --config runtime/config/webui.env db migrate
sudo systemctl restart neu-box-webui.service
```

从旧单体布局迁移时，将原配置和数据复制到 `runtime/` 后，先执行
`./run.sh --prepare-only`；确认新 WebUI 的 `/healthz` 正常后，再停用
`neu-box-master.service`。

## 版本规则

- 版本号：`src/neu_box_webui/__init__.py` 的 `__version__`
- `API_VERSION` 仅在破坏性变更时 +1
- WebUI 通过 Git 提交和 `uv.lock` 固定源码与依赖版本，不维护打包版本
