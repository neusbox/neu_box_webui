# Neu Box WebUI (Master)

Neu Box 的中心节点：节点池管理、任务转发、实验记录、Web 界面。
2026-08-25 从 `neu_box` 单体仓库（0.2.2 线）拆分独立维护。

三仓库关系：

| 仓库 | 角色 | 版本 |
|---|---|---|
| [neu_box](https://github.com/nihaopeng/neu_box) | worker（节点侧设备沙盒）+ 聚合（e2e 测试、submodule 兼容矩阵） | 0.3.0+ |
| **neu_box_webui**（本仓库） | WebUI / master | 0.0.1+ |
| [neu_box_goClient](https://github.com/nihaopeng/neu_box_goClient) | Go 客户端 `neu-sbox`（直连 worker） | 0.0.1+ |

三者只通过 HTTP 契约相交，代码零依赖（共享的 `config` / `logging_config` /
`database` 迁移引擎为本仓库自有副本，与 neu_box 仓库同源、独立演进）。

## 与 worker 的兼容

- 本仓库 `API_VERSION = 1`（`src/neu_box_webui/__init__.py`）
- 心跳时读取 worker `/status` 的 `api_version`，低于本仓库要求时打 WARNING
- 兼容矩阵：

| WebUI | 最低 worker |
|---|---|
| 0.0.1 | 0.3.0（首个上报 `api_version` 的版本） |

API 契约：[docs/master-api.md](docs/master-api.md)；
worker 侧契约见 neu_box 仓库 `docs/worker-api.md`。

## 开发

```bash
uv sync
uv run pytest tests/
```

## 构建

```bash
uv run deploy/build_release.py
# → dist/neu_box_webui-<version>-linux-<arch>.tar.gz + .sha256
```

包内容：`master/`（PyInstaller 可执行目录，含 Web 前端 static）、
`config/`（env 模板、nodes.json 模板）、`systemd/neu-box-webui.service`、
`install.sh`、`VERSION`。

## 部署

```bash
tar -xzf neu_box_webui-<v>-linux-<arch>.tar.gz
cd neu_box_webui-<v>-linux-<arch>
sha256sum -c neu_box_webui-<v>-linux-<arch>.tar.gz.sha256   # 下载后校验
sudo ./install.sh
```

- 安装到 `/opt/neu-box/webui/releases/<v>/`，链 `current`
- 首次安装生成 `/etc/neu-box/webui.env`（**检查 SECRET_KEY / ADMIN_PASS**）
- systemd 服务 `neu-box-webui.service`

### 从旧单体布局（neu_box 0.2.x）迁移

旧布局的 master 在 `/opt/neu-box/current/master/`，服务
`neu-box-master.service`，env 为 `/etc/neu-box/master.env`。迁移步骤：

```bash
# 1. 安装新 WebUI（不动旧服务）
sudo ./install.sh
# 2. 复用现有 env（含 SECRET_KEY/ADMIN_PASS/nodes.json 路径）
sudo cp /etc/neu-box/webui.env /tmp/webui.env.new   # 如首次生成
#    确认端口 25565 后:
sudo cp /etc/neu-box/master.env /etc/neu-box/webui.env   # 或直接改 unit
# 3. 切换: 停旧服务 → 新服务已起（install.sh 已启用 neu-box-webui）
sudo systemctl disable --now neu-box-master.service
# 4. 验证: curl http://127.0.0.1:25565/healthz → api_version: 1
```

**回滚**：`systemctl enable --now neu-box-master.service`
（旧布局 `/opt/neu-box/current` 保留为锚点，不会被新安装触碰）。
确认稳定运行后可手动归档 `/opt/neu-box/releases` 与 `current`。

## 版本规则

- 版本号：`src/neu_box_webui/__init__.py` 的 `__version__`
- `API_VERSION` 仅在破坏性变更时 +1
- 同一版本号不得重构建复用（部署目录按版本钉死，拒绝覆盖）
