# Neu Box WebUI (Master) API

WebUI 对**浏览器前端**（Vue SPA，构建产物在 `master/static/`）暴露的 HTTP
接口。基础路径：`http://<master-host>:25565`。

> Go 客户端（neu_box_goClient）**不经过 WebUI**：它直连 worker 的
> `NEU_BOX_URL`（见 neu_box 仓库 `docs/worker-api.md`）。WebUI 仅负责
> 节点池编排、任务转发、实验记录与 Web 界面。

**认证**：除 `/auth/login`、`/auth/me`、`/healthz`、静态资源外，所有接口要求
session 登录（cookie）。

**权限**：
- 普通用户：查看/操作自己提交的任务（`user_id` 由服务端从 session 注入，
  客户端传值无效），管理自己的实验与凭据。
- 管理员（`role=admin`）：额外可删任意任务、管理任意实验、增删节点
  （`/nodes/config/*`）、用户管理（`/admin/users*`）。
- 任务日志（`/tasks/<id>/log`、`/experiments/log/<id>`）仅任务属主或管理员
  可看；设置 `NEU_BOX_LOGS_SHARED=1` 对全体登录用户开放。

**API 版本**：`/healthz` 返回 `api_version`（当前 `2`）。
仅破坏性变更（删字段、改语义）时 +1；新增字段/端点不升版本。

## 认证

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | `{username, password}` → 建立 session；更新 `last_login_at`；停用账号拒绝 |
| GET | `/auth/me` | 当前用户；未登录 401 |
| POST | `/auth/logout` | 注销 |
| PUT | `/auth/password` | `{old_password, new_password}` |
| GET/POST | `/auth/credentials` | 各节点运行凭据 CRUD（仅本人） |
| DELETE | `/auth/credentials/<node_name>` | 删除凭据（仅本人） |

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

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/tasks` | 提交任务（202 + task_id）。`user_id` 由服务端从 session 注入，请求体中的值被忽略；同时写入 `task_submissions` |
| GET | `/tasks?node_id=` | 队列快照（转发 worker；`&mine=1` 时服务端按 user_id 过滤） |
| GET | `/tasks/mine?node_id=` | 单节点「我的任务」（转发 + 服务端按 user_id 过滤） |
| GET | `/tasks/mine` | 跨节点聚合「我的任务」（并发拉取在线节点队列）：`{groups:[{node_id, node_name, node_status, tasks[]}], offline_nodes:[{node_id, node_name}], total}` |
| DELETE | `/tasks` | 删除/终止（转发 worker）。非属主且非管理员的 id 被跳过，响应附 `denied` 列表 |
| GET | `/tasks/<task_id>` | 任务结果（`?node_id=`） |
| GET | `/tasks/<task_id>/log` | 任务日志（属主/管理员；`?node_id=&tail=N` 或 `&offset=&limit=&raw=1` 纯文本） |

`POST /tasks` 请求体字段见 `neu_box` 仓库 `docs/worker-api.md`
（`priority` 0=普通 1=赶论文）。批量提交 = 前端多次调用。

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

## 健康检查

`GET /healthz`：

```json
{"status":"ok","role":"master","api_version":2,
 "version":"0.1.0","schema_version":2}
```

## 与 worker 的兼容

WebUI 心跳时读取 worker `/status` 的 `api_version` 并记录；
低于本 WebUI 的 `API_VERSION` 时打 WARNING 日志（节点仍可用，
但可能缺少新接口）。

| WebUI | 最低 worker |
|---|---|
| 0.1.0 | 0.4.0（`/tasks`，`api_version = 2`） |

Go 客户端（neu_box_goClient）与 WebUI 无运行时依赖，它只依赖 worker API；
其兼容矩阵见 neu_box_goClient 仓库 README。
