# NeuOps (Master)

NeuOps 是 Neu Box 的中心节点与 AI 运维平台：节点池管理、任务转发、
实验记录、AI 辅助运维工单、自学习知识库和 Web 界面。
2026-08-25 从 `neu_box` 单体仓库（0.2.2 线）拆分独立维护；
Python 包与项目名已改为 `neuops`，Worker 协议保持不变。为兼容现有部署，
`NEU_BOX_*` 环境变量、`/tasks`、`/nodes`、`/experiments` 等 API 路径继续保留；
新增的 AI/工单配置使用 `NEUOPS_*` 前缀。

三仓库关系：

| 仓库 | 角色 | 版本 |
|---|---|---|
| [neu_box](https://github.com/neusbox/neu_box) | worker（节点侧设备沙盒）+ 聚合（e2e 测试、submodule 兼容矩阵） | 0.3.0+ |
| **NeuOps**（本仓库） | WebUI / master / Rroot 运维工单 | 0.2.0+ |
| [neu_box_goClient](https://github.com/neusbox/neu_box_goClient) | Go 客户端 `neu-sbox`（直连 worker） | 0.2.0+ |

三者只通过 HTTP 契约相交，代码零依赖（共享的 `config` / `logging_config` /
`database` 迁移引擎为本仓库自有副本，与 neu_box 仓库同源、独立演进）。

## 前端开发

前端为 Vue 3 SPA，源码在 `web/`（Vite 构建）。构建产物输出到
`src/neuops/master/static/` 并已提交仓库——**运行时不需要 Node**，
直接 `uv run` 即可；只有改前端时才需要构建：

```bash
cd web
npm install        # 首次
npm run build      # 输出到 master/static（index.html + assets/ + public/ 静态资源）
npm run dev        # 开发模式（:5173，API 代理到 :25565）
```

页面结构：`/` 概览 · `/ops` 运维工单 · `/ops/:id` 工单详情 ·
`/ops/knowledge` 知识库 · `/tasks` 任务（主队列/我的队列） ·
`/experiments` 实验（列表 + notebook 详情） · `/settings` 个人设置 ·
`/admin/users` 与 `/admin/nodes`（仅管理员）。SPA 路由与 API 同路径的冲突由 master 按 `Accept: text/html`
区分（浏览器导航返回 index.html，fetch 走 API）。

`web/public/` 下的 `notice.txt`、`explanation.png` 是运行时从
`/static/notice.txt` 等路径读取的静态资源，构建时自动拷贝。

## 与 worker 的兼容

- 本仓库 `API_VERSION = 2`（`src/neuops/__init__.py`）
- 心跳时读取 worker `/status` 的 `api_version`，低于本仓库要求时打 WARNING
- 兼容矩阵：

| WebUI | 最低 worker |
|---|---|
| 0.2.0 | 0.4.0（`/tasks`，`api_version = 2`） |

API 契约：[docs/master-api.md](docs/master-api.md)；
AI 运维工单说明：[docs/ops.md](docs/ops.md)；
worker 侧契约见 neu_box 仓库 `docs/worker-api.md`。

## 直接运行

NeuOps 不制作二进制或发布压缩包。目标机需要 Python 3.11+ 和
[uv](https://docs.astral.sh/uv/)，从源码同步依赖后直接启动 Python 模块。

```bash
./run.sh
```

`run.sh` 会同步锁定依赖、创建环境和节点配置、生成稳定的
`SECRET_KEY`、迁移数据库，然后启动 Python 服务。测试阶段默认
管理员账号为 `admin`，密码为 `231415926@qq.com`，公告中也会直接
显示该信息。

用户系统：
- 登录页支持自助注册（默认普通用户角色，必须填写邮箱），可用
  `NEU_BOX_ALLOW_REGISTRATION=0` 关闭；管理员/禁用账号只能由管理员
  在「用户管理」页面维护。
- 每个用户可为每个节点保存「节点上的 OS 用户名 + 密码」（设置页 →
  节点凭据）：用户名在提交任务时作为 worker 侧 `user_id` 归属显示
  （WebUI 用户名与节点 OS 用户名可以不同），密码 Fernet 加密存于
  master、仅本人可见、不会发送给 worker。

当前用户系统尚未经过严格安全测试，请勿将服务直接
暴露到公网。

## AI 运维工单（Rroot）

NeuOps 新增 `master/services/rroot/` 子系统，把所有特权操作收敛到
「工单 → 管理员审批 → 单次执行」：

- **普通用户 / AI 不能执行 root 命令**：AI 只生成建议；普通用户只能
  创建、编辑草稿并提交审批。只有管理员批准后，`rroot.executor` 才会
  以固定的 `user_id=root` 向目标 worker 提交命令。
- **审批内容不可变**：批准时冻结完整命令、参数、目标节点地址和执行
  环境，并写入 SHA-256 快照；SQLite 触发器禁止批准后修改。
- **单次授权**：审批 token 由原子状态迁移消费一次；重复执行会被拒绝。
- **执行结果完整**：worker 任务 ID、stdout、stderr、退出码、超时标志、
  耗时与完整日志副本写入 `runtime/data/master/ops-logs/`。
- **FTS5 检索**：历史工单与知识库使用 SQLite FTS5（trigram）检索，
  不引入向量数据库。
- **自学习但有监督**：管理员驳回或执行失败后，AI 只提炼带具体命令的
  知识候选；常规成功工单不自动上报（管理员可按需手动提炼）。候选本身
  也是 `knowledge` 工单，必须经管理员批准才会进入知识库列表。
- **历史数据与知识库分开处理**：历史工单/日志作为不可信数据脱敏并包装，
  防止提示词注入；已批准的知识库是管理员确认的权威知识，AI 必须无条件
  采用。命令是否安全、合法由管理员审批判断，AI 输出仍不能直接触发执行。

AI 使用 OpenAI 兼容的 `/chat/completions` 接口，可通过环境变量配置：

```bash
# OpenAI（gpt-4o-mini 只能走 /chat/completions）：
NEUOPS_AI_BASE_URL=https://api.openai.com/v1
# DeepSeek 前缀续写改用：https://api.deepseek.com/beta
NEUOPS_AI_API_KEY=...
NEUOPS_AI_MODEL=gpt-4o-mini
# 可选：超时 / 输出长度 / 温度
NEUOPS_AI_TIMEOUT=60
NEUOPS_AI_MAX_TOKENS=2048
NEUOPS_AI_TEMPERATURE=0.2
# NEUOPS_AI_ENABLED=0          # 关闭 AI（仍保留手写工单）
# NEUOPS_OPS_AUTO_LEARN=0      # 关闭自动学习候选
# NEUOPS_OPS_EXECUTE_SYNC=1    # 仅测试：同步执行
```

未配置 API Key 时，新建工单里的 AI 命令建议会返回明确提示，手写工单和确定性知识候选
仍然可用。

### 邮箱、验证码登录与管理员通知

- 新用户注册必须填写邮箱；旧用户可在「设置 → 邮箱」补充。
- 登录页支持「密码登录」和「邮箱验证码」两种方式；验证码默认 10 分钟有效。
- 新的命令工单进入待审批状态后，会通过 QQ SMTP 通知所有启用且填写了邮箱的
  管理员；通知是异步发送，不阻塞工单提交。

QQ SMTP 配置：

```bash
NEUOPS_SMTP_HOST=smtp.qq.com
NEUOPS_SMTP_PORT=465
NEUOPS_SMTP_USER=your@qq.com
NEUOPS_SMTP_PASSWORD=QQ邮箱授权码
NEUOPS_SMTP_FROM=your@qq.com
NEUOPS_SMTP_USE_SSL=1

# 初始管理员邮箱（仅当管理员邮箱为空时补充）
ADMIN_EMAIL=your@qq.com
# 审批链接外部地址（可选）
NEUOPS_PUBLIC_URL=http://192.168.1.10:25565
```

QQ 邮箱必须使用 SMTP **授权码**，不是 QQ 登录密码。

所有默认运行状态都收拢在项目的 `runtime/` 目录，首次运行自动创建：

```text
runtime/
├── config/webui.env
├── config/nodes.json
├── data/master/master.db
├── data/master/uploads/
├── data/master/experiment-logs/
├── data/master/ops-logs/
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
uv run python -m neuops.master.app --config runtime/config/webui.env db migrate
uv run python -m neuops.master.app --config runtime/config/webui.env serve
```

## 测试

```bash
uv sync --frozen
uv run pytest
```

## 可选 systemd 托管

仓库提供 `deploy/systemd/neuops.service` 示例，它直接运行源码环境
中的 Python。示例假定仓库位于
`/opt/neuops`：

```bash
cd /opt/neuops
uv sync --frozen --no-dev
sudo install -d -o neuops -g neuops /opt/neuops/runtime
sudo -u neuops ./run.sh --prepare-only

# 需要时编辑项目内的配置
sudoedit /opt/neuops/runtime/config/webui.env
sudo install -m 0644 deploy/systemd/neuops.service \
  /etc/systemd/system/neuops.service
sudo systemctl daemon-reload
sudo systemctl enable --now neuops.service
```

更新时只需拉取代码、同步锁定依赖、迁移数据库并重启服务：

```bash
git pull --ff-only
uv sync --frozen --no-dev
sudo -u neuops .venv/bin/python -m neuops.master.app \
  --config runtime/config/webui.env db migrate
sudo systemctl restart neuops.service
```

从旧单体布局迁移时，将原配置和数据复制到 `runtime/` 后，先执行
`./run.sh --prepare-only`；确认 NeuOps 的 `/healthz` 正常后，再停用
`neu-box-master.service`。

## 版本规则

- 版本号：`src/neuops/__init__.py` 的 `__version__`
- `API_VERSION` 仅在破坏性变更时 +1（包名改为 `neuops` 不改变 Worker 契约）
- NeuOps 通过 Git 提交和 `uv.lock` 固定源码与依赖版本，不维护打包版本
