# Neu Box WebUI (Master) API

WebUI 对**浏览器前端**暴露的 HTTP 接口（`js/app.js`、`js/command.js`、
`js/experiment.js`）。
基础路径：`http://<master-host>:25565`。

> Go 客户端（neu_box_goClient）**不经过 WebUI**：它直连 worker 的
> `NEU_BOX_URL`（见 neu_box 仓库 `docs/worker-api.md`）。WebUI 仅负责
> 节点池编排、任务转发、实验记录与 Web 界面。

**认证**：除 `/auth/login`、`/auth/me`、`/healthz`、静态资源外，所有接口要求
session 登录（cookie）。

**API 版本**：`/healthz` 返回 `api_version`（当前 `2`）。
仅破坏性变更（删字段、改语义）时 +1；新增字段/端点不升版本。

## 认证

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | `{username, password}` → 建立 session |
| GET | `/auth/me` | 当前用户；未登录 401 |
| POST | `/auth/logout` | 注销 |
| PUT | `/auth/password` | `{old_password, new_password}` |
| GET/POST | `/auth/credentials` | 各节点运行凭据 CRUD |
| DELETE | `/auth/credentials/<node_name>` | 删除凭据 |

## 节点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/nodes/get_all_nodes` | 全部节点（含实时资源） |
| GET | `/nodes/<node_id>/status` | 代理 worker `/status` |
| GET | `/nodes/<node_id>/sandboxes` | 代理 worker `/sandbox/list`（终端+任务沙盒） |
| GET | `/nodes/config` | 已配置节点列表 |
| POST | `/nodes/config/add` | `{name, host, port}` |
| POST | `/nodes/config/remove` | `{name}` |

## 命令任务（浏览器前端）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/tasks` | 提交任务到指定节点队列，返回 202 + task_id（批量 = 前端多次调用） |
| GET | `/tasks?node_id=` | 队列快照 |
| DELETE | `/tasks` | 删除/终止任务（排队/运行） |
| GET | `/tasks/<task_id>` | 任务结果 |
| GET | `/tasks/<task_id>/log` | 任务日志 |

`POST /tasks` 请求体字段见 `neu_box` 仓库 `docs/worker-api.md`
（WebUI 原样转发到目标 worker；`priority` 0=普通 1=赶论文）。

## 实验记录

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/experiments/` | 创建 |
| GET | `/experiments/` | 列表（?folder_id= 过滤） |
| GET/PUT | `/experiments/<id>` | 详情 / 更新 |
| DELETE | `/experiments/<id>` | 删除 |
| POST | `/experiments/upload-image` | 上传图片 |
| GET/POST | `/experiments/folders` | 文件夹列表 / 创建 |
| PUT/DELETE | `/experiments/folders/<fid>` | 重命名 / 删除 |
| GET | `/experiments/log/<task_id>` | 关联任务日志 |

## 健康检查

`GET /healthz`：

```json
{"status":"ok","role":"master","api_version":2,
 "version":"0.1.0","schema_version":1}
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
