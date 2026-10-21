# NeuOps (Master) API

NeuOps 对**浏览器前端**（Vue SPA，构建产物在 `master/static/`）暴露的 HTTP
接口。基础路径：`http://<master-host>:25565`。AI 运维工单（Rroot）的完整
说明见 [ops.md](ops.md)。

> Go 客户端（neu_box_goClient）**不经过 WebUI**：它直连 worker 的
> `NEU_BOX_URL`（见 neu_box 仓库 `docs/worker-api.md`）。WebUI 仅负责
> 节点池编排、任务转发、实验记录与 Web 界面。

**认证**：除 `/auth/login`、`/auth/register*`、`/auth/me`、`/healthz`、静态资源外，
所有接口要求 session 登录（cookie）。

**权限**：
- 普通用户：查看/操作自己提交的任务（任务归属见「命令任务」），
  管理自己的实验与节点凭据。
- 管理员（`role=admin`）：额外可删任意任务、管理任意实验、增删节点
  （`/nodes/config/*`）、用户管理（`/admin/users*`）。
- 任务日志（`/tasks/<id>/log`、`/experiments/log/<id>`）仅任务属主或管理员
  可看；设置 `NEU_BOX_LOGS_SHARED=1` 对全体登录用户开放。
- 自助注册默认开放（`NEU_BOX_ALLOW_REGISTRATION=1`），仅能创建普通
  用户；设 `0` 关闭。

**API 版本**：`/healthz` 返回 `api_version`（当前 `2`）。
仅破坏性变更（删字段、改语义）时 +1；新增字段/端点不升版本。

## 认证

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | 自助注册 `{username, password, email}`（邮箱必填，普通用户角色，成功后自动登录）；开关关闭 403 |
| GET | `/auth/register` | 注册开关状态 `{allowed}`（登录页据此显示/隐藏注册入口） |
| POST | `/auth/login` | `{username, password}` → 建立 session；更新 `last_login_at`；停用账号拒绝 |
| POST | `/auth/email-code` | `{email}` → 发送 6 位邮箱验证码（不泄露邮箱是否存在） |
| POST | `/auth/email-login` | `{email, code}` → 邮箱验证码登录，成功后标记 `email_verified_at` |
| PUT | `/auth/email` | 登录用户补充/修改自己的邮箱；邮箱唯一 |
| GET | `/auth/me` | 当前用户；未登录 401 |
| POST | `/auth/logout` | 注销 |
| PUT | `/auth/password` | `{old_password, new_password}` |

### 节点凭据（节点上的 OS 用户名 + 密码，仅本人）

WebUI 用户名不一定等于节点 OS 用户名。每个用户可为每个节点维护自己的
OS 账号；提交任务时以凭据用户名作为 worker 侧 `user_id`（未设置则用
WebUI 用户名）。密码 Fernet 加密存于 master（密钥由 `SECRET_KEY` 派生），
仅本人可查询，**不会发送给 worker**（供用户登录节点参考）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/auth/credentials` | 列表（不含密码，仅 `has_password` 标志） |
| PUT | `/auth/credentials/<node_name>` | 保存/更新 `{username, password?}`；password 缺省=不改，空串=清除 |
| GET | `/auth/credentials/<node_name>/password` | 查看已存密码（明文，仅本人；未存则 `password: null`） |
| DELETE | `/auth/credentials/<node_name>` | 删除凭据 |

## 节点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/nodes/get_all_nodes` | 全部节点（含实时资源） |
| GET | `/nodes/<node_id>/status` | 代理 worker `/status` |
| GET | `/nodes/<node_id>/sandboxes` | 代理 worker `/sandbox/list`（终端+任务沙盒） |
| GET | `/nodes/config` | 已配置节点列表 |
| POST | `/nodes/config/add` | `{name, host, port}` — **仅管理员** |
| POST | `/nodes/config/remove` | `{name}` — **仅管理员** |

## 命令任务

任务物理上在 worker 节点队列中，WebUI 原样转发；`task_submissions` 表
（master 侧）记录提交历史，用于 dashboard 统计与日后审计。

**任务归属（user_id）**：提交时服务端注入——该节点已配置凭据用户名则用
凭据用户名，否则用 WebUI 用户名；客户端传值一律忽略。因此「我的任务」/
删除/日志的属主判定 = 任务的 `user_id` ∈ {我的 WebUI 用户名, 我在这节点的
凭据用户名}。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/tasks` | 提交任务（202 + task_id）。`user_id` 按上文规则由服务端注入；同时写入 `task_submissions` |
| GET | `/tasks?node_id=` | 队列快照（转发 worker；`&mine=1` 时服务端按 user_id 过滤） |
| GET | `/tasks/mine?node_id=` | 单节点「我的任务」（转发 + 服务端按 user_id 过滤） |
| GET | `/tasks/mine` | 跨节点聚合「我的任务」（并发拉取在线节点队列）：`{groups:[{node_id, node_name, node_status, tasks[]}], offline_nodes:[{node_id, node_name}], total}` |
| DELETE | `/tasks` | 删除/终止（转发 worker）。非属主且非管理员的 id 被跳过，响应附 `denied` 列表。task_ids 混入沙盒名（`sbx_*.slice`）时走 `/sandbox/release` 销毁（左栏沙盒卡的「销毁」按钮即此路径） |
| GET | `/tasks/<task_id>` | 任务结果（`?node_id=`） |
| GET | `/tasks/<task_id>/log` | 任务日志（属主/管理员；`?node_id=&tail=N` 或 `&offset=&limit=&raw=1` 纯文本） |

`POST /tasks` 请求体字段见 `neu_box` 仓库 `docs/worker-api.md`
（`priority` 0=普通 1=赶论文）。批量提交 = 前端多次调用。

> **特权命令限制**：普通任务队列（`/tasks`）拒绝 `user_id=root`。
> root 命令必须走 `/ops` 工单：管理员批准后由 Rroot 执行器以固定 root
> 身份单次执行。该限制适用于普通用户、管理员和 AI。

## 实验记录

`created_by` 由服务端从 session 注入。PUT/DELETE 仅属主或管理员。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/experiments/` | 创建 |
| GET | `/experiments/` | 列表（`?folder_id=` / `?q=` / `?scope=mine` 过滤） |
| GET/PUT | `/experiments/<id>` | 详情 / 更新（属主/管理员） |
| DELETE | `/experiments/<id>` | 删除（属主/管理员） |
| POST | `/experiments/upload-image` | 上传图片 |
| GET/POST | `/experiments/folders` | 文件夹树 / 创建 |
| PUT/DELETE | `/experiments/folders/<fid>` | 重命名 / 删除（级联移入根） |
| GET | `/experiments/log/<task_id>` | 关联任务日志（属主/管理员） |

## 用户管理（仅管理员）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/users` | 用户列表（id/username/role/created_at/is_active/last_login_at） |
| POST | `/admin/users` | 创建 `{username, password, role?}` |
| PUT | `/admin/users/<id>` | 改角色/启用停用（不能停用/降级自己，不能移除最后一个有效管理员） |
| POST | `/admin/users/<id>/password` | 重置密码 |

## AI 运维工单（Rroot）

基础路径 `/ops`。普通用户只能查看自己的工单；批准/驳回/执行/批量学习
仅管理员。详细字段与安全模型见 [ops.md](ops.md)。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/ops/stats` | 工单统计、知识库数量、AI 状态 |
| GET | `/ops/tickets` | 列表（`?status=&kind=&q=`） |
| POST | `/ops/tickets` | 创建命令/咨询/知识工单 |
| GET/PUT | `/ops/tickets/<id>` | 详情 / 修改草稿或待审批 |
| POST | `/ops/tickets/<id>/submit` | 提交审批 |
| POST | `/ops/tickets/<id>/cancel` | 取消 |
| POST | `/ops/tickets/<id>/approve` | 管理员批准（可 `execute: true`） |
| POST | `/ops/tickets/<id>/reject` | 管理员驳回 |
| POST | `/ops/tickets/<id>/execute` | 管理员执行已批准命令（单次） |
| GET | `/ops/tickets/<id>/log` | 执行日志（`?raw=1` 纯文本） |
| POST | `/ops/assistant/suggest` | 根据 `{node_id, request}` 生成命令建议；不创建工单、不执行 |
| POST | `/ops/assistant/agent` | SSE 流：AI 只读工具调用进度 + 最终命令建议 |
| GET | `/ops/knowledge` | 已批准知识列表 |
| GET | `/ops/knowledge/<id>` | 知识详情 |
| PUT | `/ops/knowledge/<id>` | **仅管理员**直接修改知识条目，不经过审批 |
| DELETE | `/ops/knowledge/<id>` | **仅管理员**直接删除知识条目，不经过审批 |
| POST | `/ops/knowledge/learn` | 管理员手动触发学习 |

## 健康检查

`GET /healthz`：

```json
{"status":"ok","service":"neuops","role":"master","api_version":2,
 "version":"0.2.0","schema_version":5}
```

## 与 worker 的兼容

WebUI 心跳时读取 worker `/status` 的 `api_version` 并记录；
低于本 WebUI 的 `API_VERSION` 时打 WARNING 日志（节点仍可用，
但可能缺少新接口）。

| WebUI | 最低 worker |
|---|---|
| 0.2.0 | 0.4.0（`/tasks`，`api_version = 2`） |

Go 客户端（neu_box_goClient）与 WebUI 无运行时依赖，它只依赖 worker API；
其兼容矩阵见 neu_box_goClient 仓库 README。
