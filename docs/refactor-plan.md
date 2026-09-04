# WebUI 重构方案：多页面 + 用户队列

> 目标：① 功能从单页拆分到多个页面；② 完善用户系统，每个用户管理自己的队列，同时可查看主队列。
>
> 约束：不引入构建步骤（仓库原则：`uv` + 源码直跑）；不破坏 worker 协议
>（三仓库仅通过 HTTP 契约相交，worker 保持 `api_version = 2` 不变）。

## 1. 现状与问题

### 后端（现状良好，局部缺口）

- Flask + SQLite（WAL）+ waitress，blueprint 按 auth/command/experiment/nodes 拆分；
  迁移引擎支持 `.sql`/`.py` 两种迁移（`0001_initial.sql` 已就位）。
- 用户系统只有一半：`users` 表 + bcrypt + session 已存在，但
  - 没有用户管理 API（建用户只能走 CLI `admin reset-password` 兜底）；
  - 没有角色约束：任意登录用户都能增删 nodes.json 节点；
  - 实验无所有权校验（`created_by` 由前端自由填写）。
- **任务归属是前端自由文本**：`POST /tasks` 的 `user_id` 直接取前端输入框
（localStorage 记忆 + 凭据自动填入），可冒充他人，也无法做"我的队列"。

### 前端（主要痛点）

- 单页单体：`index.html`（~400 行）塞了三栏布局 + 8 个弹窗
（节点管理/通知/重跑/登录/账户/实验…）；
- JS 三个文件共 ~2900 行，跨文件共享全局变量
（`experiment.js` 直接引用 `app.js` 的 `state`、`_taskMeta` 等）；
- "任务/实验"模式切换其实是两个独立功能，只是共享节点列表和日志面板；
- 账户管理、节点管理混在主界面弹窗里，没有独立入口。

## 2. 核心架构决策

1. **不在 master 建"队列"。** 任务继续以 worker 的节点队列为唯一事实来源；
   "用户队列" = 服务端过滤视图（按 `user_id` 过滤 + 跨节点聚合）。
   worker、Go 客户端零改动。
2. **任务归属由服务端注入。** `POST /tasks` 忽略前端传来的 `user_id`，
   一律写入 session 用户的 username（与 worker 现有 `user_id` 字符串字段一致，
   worker 协议不变）。
3. **master 增加轻量 `task_submissions` 登记表**（可选但推荐）：
   记录提交（用户名、节点、命令、时间）。用途：
   - "我的任务历史"——任务从 worker 队列消失后仍可查；
   - dashboard 统计；
   - 将来若需要全局任务账本，在此表上扩展，不动 worker。
4. **前端 MPA，不 SPA。** Flask 用 Jinja2 渲染共享壳（侧边导航 + 用户 + 主题），
   每页一个模板 + 一个页面 JS 模块；无构建、无框架，部署方式不变。

## 3. 用户系统与权限

### 角色模型

| 能力 | 普通用户 | 管理员 |
|---|---|---|
| 提交任务（自动归属本人） | ✓ | ✓ |
| 查看主队列（节点全量，只读） | ✓ | ✓ |
| 删除 / 重跑**自己的**任务 | ✓ | ✓ |
| 删除**他人**任务 | ✗ | ✓ |
| 查看任务日志 | 仅本人 | 全部 |
| 创建实验 | ✓ | ✓ |
| 编辑 / 删除实验 | 仅本人 | 全部 |
| 节点配置管理（nodes.json） | ✗ | ✓ |
| 用户管理 | ✗ | ✓ |

所有权限**服务端强制**，前端只负责隐藏入口。

### 服务端强制点

1. `POST /tasks`：服务端写 `user_id = session 用户名`；同时写 `task_submissions`。
2. `DELETE /tasks`：对每个 `task_id` 先向 worker 取任务元数据，
   校验 `user_id == 本人` 或 `role == admin`；逐条裁决，返回被拒绝项。
3. `GET /tasks/<id>/log`：仅本人或 admin（日志可能含敏感信息）。
   任务元数据 `GET /tasks/<id>` 所有人可看（主队列需要展示）。
   如实验室需要互相看日志，提供配置开关 `NEU_BOX_LOGS_SHARED`（默认关）。
4. 实验：创建时 `created_by` 取 session；PUT/DELETE 校验 owner 或 admin；
   GET 全员可看，支持 `?scope=mine` 过滤。
5. `/nodes/config/*`：`login_required` 升级为 `admin_required`。
6. 登录态：校验 `users.is_active`，禁用用户无法登录；
   已登录的禁用用户在后续请求被踢出（`login_required` 里顺带查一次）。

### 用户管理 API（新增，admin）

```
GET   /admin/users                 用户列表（不含哈希）
POST  /admin/users                 {username, password, role} 创建
PUT   /admin/users/<id>            {role?, is_active?} 改角色 / 禁用
POST  /admin/users/<id>/password   {password} 重置密码
```

- CLI `admin reset-password` 保留为最后恢复通道（admin 密码丢失场景），不改动。
- 可选：`POST /auth/register` 自助注册，配置开关 `NEU_BOX_ALLOW_REGISTER`
  （默认关；实验室建议由 admin 创建）。

### 我的队列 / 主队列 API

```
GET /tasks?node_id=X              主队列：节点全量（现有端点，语义不变，全员可读）
GET /tasks/mine?node_id=X         该节点上我的任务（服务端过滤 user_id==本人）
GET /tasks/mine                   跨节点聚合"我的队列"：
                                  ThreadPool 并发拉各在线节点队列 → 过滤 → 合并，
                                  返回按节点分组 [{node_name, node_id, tasks:[...]}]
```

- 不带 `node_id` 的 `/tasks/mine` 是"每用户管理自己的队列"的核心：
  不依赖节点选择，全网我的任务一屏可见、可删、可看日志。
- 聚合容错：单节点 5s 超时，返回已拿到的部分，离线节点单独列出。
- dashboard 的统计（排队中/运行中/今日完成）复用同一聚合逻辑。

### 数据模型（迁移 0002）

```sql
-- master/migrations/0002_user_system.sql
ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN last_login_at REAL;

-- 提交登记表：我的任务历史 / dashboard（不替代 worker 队列）
CREATE TABLE task_submissions (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    user_name  TEXT NOT NULL,   -- WebUI 用户名，即转发给 worker 的 user_id
    command    TEXT,
    created_at REAL
);
CREATE INDEX idx_ts_user ON task_submissions(user_name, created_at);
CREATE INDEX idx_ts_node ON task_submissions(node_id, created_at);
```

- `db.py` 的 `REQUIRED_COLUMNS` / `REQUIRED_INDEXES` 同步更新。
- 历史任务：重构前提交的 `user_id` 是自由文本，"我的队列"匹配不到，可接受；
  可选提供一次性脚本按用户名匹配回填 `task_submissions`。

## 4. 前端多页面方案

### 页面划分

| 路由 | 页面 | 内容 |
|---|---|---|
| `/login` | 登录 | 独立页面（取代登录弹窗）；`?next=` 登录成功回跳 |
| `/` | 概览 dashboard | 节点资源总览、我的任务汇总（排队/运行/完成）、通知（notice.txt） |
| `/tasks` | 任务 | 左：提交表单（**移除自由文本用户名框**，归属自动）；右：队列区两个 tab「我的队列 / 全部队列」；日志查看器保留在本页 |
| `/experiments` | 实验列表 | 文件夹树 + 搜索 + 列表；「我的 / 全部」过滤 |
| `/experiments/<id>` | 实验详情 | 渲染 + 编辑（markdown blocks + 图片上传）；原实验弹窗升级为页面 |
| `/settings` | 我的设置 | 修改密码、各节点凭据（原"账户管理"弹窗） |
| `/admin/users` | 用户管理（admin） | 创建 / 禁用 / 重置密码 / 改角色 |
| `/admin/nodes` | 节点管理（admin） | nodes.json 增删（原"节点管理"弹窗） |

### 目录结构（static 重组 + 新增 templates）

```
src/neu_box_webui/master/
├── templates/                  # 新增（Jinja2，只渲染壳与页面骨架）
│   ├── base.html               # 侧边导航、用户菜单、主题、toast 容器
│   ├── login.html
│   ├── dashboard.html
│   ├── tasks.html
│   ├── experiments.html
│   ├── experiment_detail.html
│   ├── settings.html
│   ├── admin_users.html
│   └── admin_nodes.html
└── static/
    ├── css/
    │   ├── base.css            # 变量/主题/按钮/表单/弹窗/toast（从 style.css 拆）
    │   ├── layout.css          # 侧边导航 + 栏布局
    │   ├── tasks.css           # 原 command.css
    │   ├── experiments.css     # 原 experiment.css
    │   └── admin.css
    ├── js/
    │   ├── common.js           # api() fetch 封装（401→/login?next=）、
    │   │                       #   当前用户与导航渲染、主题、toast、
    │   │                       #   格式化函数、节点列表组件（NodePicker）
    │   ├── dashboard.js
    │   ├── tasks.js            # 原 app.js(提交/stepper) + command.js(队列/日志)
    │   ├── experiments.js
    │   ├── experiment_detail.js
    │   ├── settings.js
    │   ├── admin_users.js
    │   └── admin_nodes.js
    └── notice.txt
```

- `common.js` 是唯一跨页共享代码；页面 JS 之间零耦合，
  消除现在 `experiment.js` 伸手进 `app.js` 全局变量的情况。
- 401 处理从"弹窗"改为"跳转 `/login?next=当前页`"，逻辑收敛在 `api()` 一处。
- 导航按角色渲染：非 admin 看不到「管理」分组（服务端模板变量控制，
  不依赖前端 JS 判断）。

## 5. 后端代码组织（目标）

```
master/api/
├── auth.py        登录/登出/me/密码/凭据（+可选 register）
│                  login_required 内增加 is_active 校验
├── permissions.py 新增：require_admin 装饰器
├── tasks.py       由 command.py 更名：
│                  POST   注入归属 + 写 task_submissions
│                  GET    主队列（不变）
│                  GET    /tasks/mine（新增，含跨节点聚合）
│                  DELETE 归属校验
│                  GET    /tasks/<id>、/tasks/<id>/log（log 加 owner 校验）
├── experiment.py  加 owner/admin 校验
├── nodes.py       /config/* 改 admin_required
└── admin.py       新增：用户管理
master/services/
├── db.py          + users.is_active/last_login_at、task_submissions CRUD
└── nodes_pool.py  不动
master/migrations/ 0002_user_system.sql
```

`app.py` 注册页面路由：每个页面 `@login_required_page`（未登录 302 → `/login?next=`），
admin 页面 403 给非 admin。

## 6. 顺带的安全加固

- 登录失败限流：内存计数（每用户名 5 分钟 10 次 → 429），不引第三方依赖。
- `NEU_BOX_COOKIE_SECURE` 开关：HTTPS 代理后置真（`SESSION_COOKIE_SECURE`）。
- 默认 admin 密码 + 公告提示保留现状（README 已注明测试阶段），
  但登录限流降低暴力破解面。
- 图片上传维持登录制 + MIME/大小校验（现状已有）。

## 7. 分阶段实施

每阶段独立可提交、可回滚；P1–P2 不动前端，P3–P5 不改既有后端契约（只新增端点）。

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P1 权限地基**（纯后端） | 迁移 0002；`tasks.py`：归属注入 + `task_submissions` + 删除/日志归属校验；实验 owner 校验；`/nodes/config/*` → admin；`require_admin`；`is_active` 登录校验 | pytest 权限矩阵用例（普通用户不能删他人任务、不能管节点、不能改他人实验…）；现有测试全绿 |
| **P2 用户管理 API** | `/admin/users` CRUD + 重置密码；（可选 register 开关） | API 测试通过；curl 可走通全流程 |
| **P3 页面拆分**（功能不变） | `templates/` + `common.js`；任务三栏 → `/tasks`；实验 → `/experiments`；登录独立页；账户/节点管理弹窗先移到对应页面 | 功能回归：提交/队列/日志/批量/实验/凭据/节点管理/主题全部正常 |
| **P4 我的队列** | `GET /tasks/mine`（单节点 + 聚合）；`/tasks` 页「我的 / 全部」tab；dashboard 概览页 | 多节点下我的队列正确；3+ 节点聚合延迟可接受；离线节点容错 |
| **P5 设置/详情页 + 打磨** | `/settings`、`/experiments/<id>` 独立页；通知移入 dashboard；admin 两页 | 页面跳转、角色导航差异、深浅主题一致 |
| **P6 安全收尾 + 文档** | 登录限流、cookie 开关、`docs/master-api.md` 与 README 更新 | 文档与代码一致；`/healthz` schema_version = 2 |

## 8. 风险与取舍

1. **`/tasks/mine` 聚合是 fan-out 请求**：节点多或有离线节点时变慢 →
   每节点 5s 超时 + 并发拉取 + 部分成功返回（缺的节点标记 offline）。
2. **历史任务归属**：自由文本 `user_id` 无法精确归属；
   需要时提供一次性回填脚本（按用户名匹配写 `task_submissions`）。
3. **日志查看收紧**：现在任意登录用户可看任意日志 → 默认仅 owner/admin；
   用 `NEU_BOX_LOGS_SHARED` 开关保留旧行为选项。
4. **Jinja 模板化**：Flask 从纯静态变为模板 + 静态，部署方式不变
   （同一 `template_folder`/`static_folder`，run.sh/systemd 不需要改）。
5. **暂不做**（保留扩展路径）：master 侧完整任务账本、任务跨节点搜索统计、
   文件夹按用户私有化。若未来需要，基于 `task_submissions` + worker 心跳上报
   队列快照演进，不影响本次设计。

## 9. 明确不做的事（避免范围蔓延）

- 不迁移 React/Vue 等框架，不引入 npm/构建链。
- 不改 worker API、不升 `API_VERSION`（对 worker 仍是 api_version 2 契约）。
- 不改 Go 客户端路径（它直连 worker，与本重构无关）。
- 不引入 Redis/外部队列；SQLite + worker 队列仍是全部状态。
