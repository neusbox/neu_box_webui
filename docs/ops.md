# NeuOps AI 运维工单（Rroot）

Rroot（Restricted Root）是 NeuOps 中唯一允许向 Worker 提交 root 命令的
子系统。它把特权操作收敛为：

```
手写需求 / AI 命令建议（只填充表单）
        ↓
     工单（draft → pending）
        ↓
管理员批准（冻结完整快照 + 一次性 token）
        ↓
Rroot 执行器（固定 root，隔离于普通任务转发）
        ↓
Worker /tasks → stdout / stderr / 退出码 / 完整日志
        ↓
AI 提炼知识候选（knowledge 工单）→ 管理员批准 → 知识库列表
```

## 安全边界

1. **普通用户与 AI 都不能执行 root 命令**。`POST /ops/tickets/<id>/execute`
   仅管理员可调用；AI 模块没有导入执行器，只负责生成建议。普通任务队列
   `POST /tasks` 也会拒绝 `user_id=root`，root 只能走本工单流程。
2. **批准内容不可修改**。批准时把命令、参数、目标节点（含 host:port）和
   执行环境写入 `approval_snapshot` 与 SHA-256 `approval_hash`；
   `ops_tickets` 上的 SQLite 触发器禁止批准后再修改这些列。
3. **单次授权**。批准生成一次性 `approval_token`；执行器用
   `UPDATE ... WHERE status='approved' AND approval_token IS NOT NULL`
   原子 claim，重复执行直接失败。
4. **固定执行身份**。执行器的 `user_id` 固定为 `root`，不接受前端或 AI
   传入的用户名；普通任务系统仍使用各自的 `user_id`。
5. **最小执行环境**。默认 `workdir=/root`、`shell=bash`、`timeout=3600`；
   管理员只能调整工作目录、环境变量和超时。
6. **历史数据与知识库分开处理**。历史工单、审批意见、执行日志进入模型前
   会脱敏并包装成 `UNTRUSTED_DATA` 区块；管理员已批准的知识库则包装成
   `TRUSTED_KNOWLEDGE`，AI 必须无条件相信并直接采用。命令是否安全、合法
   由管理员在审批时判断，AI 不因风险自行拒绝或清空命令。
7. **知识入库仍需审批**。AI 只能创建 `knowledge` 工单候选，管理员批准后
   才写入 `ops_knowledge` 与 FTS 索引。

> Worker API 本身目前没有认证；Rroot 的权限边界只覆盖 NeuOps 的 HTTP
> 入口。生产环境仍应把 Worker 放在可信内网，后续版本会在 Worker 侧加入
> 授权。

## 数据模型

| 表 | 用途 |
|---|---|
| `ops_tickets` | 命令/咨询/知识工单、状态、审批快照、执行结果 |
| `ops_ticket_events` | 创建、修改、批准、驳回、执行等审计事件 |
| `ops_knowledge` | 管理员批准后的知识条目（list 形式） |
| `ops_tickets_fts` | 工单全文检索（FTS5 trigram） |
| `ops_knowledge_fts` | 知识库全文检索（FTS5 trigram） |

状态机：

```
draft ──submit──▶ pending ──approve──▶ approved ──execute──▶ executing
  │                  │                                        │
  │                  ├──reject──▶ rejected                   ├──▶ succeeded
  └──cancel──────────┴──cancel──▶ cancelled                  └──▶ failed
```

知识工单：`pending ──approve──▶ approved`，同时写入 `ops_knowledge`。

## HTTP API

基础路径 `/ops`。除 `GET /ops/knowledge*` 外，工单详情仅属主或管理员可见；
批准/驳回/执行/批量学习仅管理员。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/ops/stats` | 工单状态统计、知识库数量、AI 配置状态 |
| GET | `/ops/tickets` | 列表；`?status=&kind=&q=&limit=`。普通用户自动只看自己的 |
| POST | `/ops/tickets` | 创建命令/咨询/知识工单；用户知识候选强制 pending |
| GET | `/ops/tickets/<id>` | 详情 + 审计事件 + can_approve/can_execute |
| PUT | `/ops/tickets/<id>` | 修改草稿/待审批工单；管理员必须填写 `reason` |
| POST | `/ops/tickets/<id>/submit` | 草稿提交审批 |
| POST | `/ops/tickets/<id>/cancel` | 取消草稿/待审批 |
| POST | `/ops/tickets/<id>/approve` | 管理员批准；`{reason, command?, exec_env?, execute?}` |
| POST | `/ops/tickets/<id>/reject` | 管理员驳回；`{reason}` 必填 |
| POST | `/ops/tickets/<id>/execute` | 管理员执行已批准命令；单次 |
| GET | `/ops/tickets/<id>/log` | 执行日志；`?raw=1` 返回纯文本 |
| POST | `/ops/assistant/suggest` | `{node_id, request}` → 命令建议；不创建工单、不执行 |
| GET | `/ops/knowledge` | 已批准知识列表；`?q=&kind=` |
| GET | `/ops/knowledge/<id>` | 知识详情 |
| POST | `/ops/knowledge/learn` | 管理员手动触发学习；`{ticket_id?}` |

### `POST /ops/tickets`

命令/咨询工单：

```json
{
  "kind": "command",
  "node_id": "node-uuid",
  "title": "清理 /var 大文件",
  "request": "定位 /var 下超过 1GB 的文件并确认",
  "command": "du -ah /var | sort -rh | head -20",
  "exec_env": {
    "user": "root",
    "shell": "bash",
    "login": true,
    "workdir": "/root",
    "timeout_seconds": 600,
    "env": {}
  },
  "params": {"cpu": 0, "memory": 0, "mem_unit": "GB", "device_num": 0},
  "status": "pending"
}
```

知识候选：

```json
{
  "kind": "knowledge",
  "knowledge_payload": {
    "title": "GPU 节点 /var 清理步骤",
    "content": "先 du 定位，再确认文件归属……",
    "kind": "runbook",
    "tags": ["磁盘", "清理"]
  }
}
```

### `POST /ops/tickets/<id>/approve`

```json
{
  "reason": "只读排查命令，低风险",
  "command": "du -ah /var | sort -rh | head -20",
  "exec_env": {
    "workdir": "/root",
    "timeout_seconds": 600,
    "env": {}
  },
  "execute": true
}
```

`execute=true` 会在批准成功后立即启动执行；也可以稍后单独调用
`POST /ops/tickets/<id>/execute`。批准后返回的 `approval_snapshot` 包含
完整命令、参数、目标节点和执行环境。

## 执行日志

- Worker 的日志把 stdout/stderr 合并；执行器用随机 marker 包裹命令，
  在临时文件中分离两路输出，再解析为 `exec_result.stdout` 和
  `exec_result.stderr`。
- 完整日志副本写入 `NEUOPS_OPS_LOG_DIR`（默认
  `runtime/data/master/ops-logs/<ticket_id>.log`），不受 Worker 任务
  清理影响。
- 数据库中的 stdout/stderr 默认截断到 200000 字符
  （`NEUOPS_OPS_MAX_OUTPUT`），完整内容以日志文件为准。

## AI 配置

OpenAI 兼容接口：

```bash
# OpenAI（gpt-4o-mini 只能走 /chat/completions）：
NEUOPS_AI_BASE_URL=https://api.openai.com/v1
# DeepSeek 前缀续写改用：https://api.deepseek.com/beta
NEUOPS_AI_API_KEY=sk-...
NEUOPS_AI_MODEL=gpt-4o-mini
NEUOPS_AI_TIMEOUT=60
NEUOPS_AI_MAX_TOKENS=2048
NEUOPS_AI_TEMPERATURE=0.2
NEUOPS_AI_ENABLED=1
```

统一使用 Chat Completion（`/chat/completions`），它本身同时承担普通对话
和前缀续写，不需要再配置 chat/completion 模式。JSON 生成时最后一条消息
固定为：

```json
{"role": "assistant", "content": "{", "prefix": true}
```

模型从 `{` 之后续写 JSON，因此返回内容的首个非空白字符稳定为 `{`。
DeepSeek 需要把 `NEUOPS_AI_BASE_URL` 设为 `https://api.deepseek.com/beta`；
OpenAI 使用 `https://api.openai.com/v1`，`gpt-4o-mini` 本身不支持旧版
`/completions`，只支持 `/chat/completions`。若服务端不支持 `prefix`
字段，会自动降级为 assistant prefill、JSON mode、普通 chat，不需要手工
切换配置。

未配置 Key 时：

- 新建工单里的「AI命令建议」会返回错误提示，命令输入框保持为空；
- 自学习使用确定性 fallback，从驳回原因/执行结果生成知识候选；
- 手写工单与审批流程完全可用。

「AI命令建议」是严格模式：接口必须返回非空 `command`，否则会自动重试
两次；仍然为空时返回 HTTP 502，不会把澄清文字或空命令当作成功结果。

## 自学习

触发时机：

- 执行成功 / 失败后；
- 管理员驳回后；
- 管理员调用 `POST /ops/knowledge/learn`。

流程：

1. 收集工单字段、审批意见、驳回原因、执行结果和审计事件；
2. 脱敏后作为不可信数据送入 AI，输出 1-3 条知识候选 JSON；
3. 去重（`ops_knowledge.content_hash` + 同来源 pending 候选）；
4. 创建 `kind=knowledge`、`status=pending` 的工单；
5. 管理员在工单列表/详情审批；批准后写入 `ops_knowledge` 和 FTS，
   在前端知识库以列表形式展示。

可用 `NEUOPS_OPS_AUTO_LEARN=0` 关闭自动候选；
`NEUOPS_OPS_LEARN_SYNC=1` 仅用于测试同步学习。
