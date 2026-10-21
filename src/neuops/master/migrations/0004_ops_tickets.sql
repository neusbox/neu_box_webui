-- NeuOps 运维工单 / 知识库 schema。
--
-- 设计要点：
--   * 工单是唯一入口：普通用户、管理员、AI 都只能创建/修改工单，
--     真正的 root 执行只发生在管理员批准后的 Rroot 执行器中。
--   * ops_tickets 保存审批时的完整快照（命令、参数、节点、执行环境），
--     批准后由触发器冻结，保证批准内容不可再修改。
--   * FTS5 使用 trigram tokenizer，支持中英文子串检索；短查询由应用层
--     回退到 LIKE。

CREATE TABLE ops_tickets (
    id                    TEXT PRIMARY KEY,
    kind                  TEXT NOT NULL DEFAULT 'command'
                          CHECK (kind IN ('command', 'consult', 'knowledge')),
    status                TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN (
                              'draft', 'pending', 'approved', 'executing',
                              'succeeded', 'failed', 'rejected', 'cancelled'
                          )),
    title                 TEXT NOT NULL DEFAULT '',
    request               TEXT NOT NULL DEFAULT '',
    command               TEXT NOT NULL DEFAULT '',
    target_node_id        TEXT NOT NULL DEFAULT '',
    target_node_name      TEXT NOT NULL DEFAULT '',
    target_node_address   TEXT NOT NULL DEFAULT '',
    exec_env              TEXT NOT NULL DEFAULT '{}',
    params                TEXT NOT NULL DEFAULT '{}',
    ai_suggestion         TEXT NOT NULL DEFAULT '{}',
    ai_model              TEXT NOT NULL DEFAULT '',
    risk_level            TEXT NOT NULL DEFAULT 'unknown',
    knowledge_payload     TEXT NOT NULL DEFAULT '{}',
    admin_note            TEXT NOT NULL DEFAULT '',
    approval_snapshot     TEXT NOT NULL DEFAULT '',
    approval_hash         TEXT NOT NULL DEFAULT '',
    approval_token        TEXT,
    created_by            TEXT NOT NULL,
    created_by_id         TEXT NOT NULL DEFAULT '',
    created_at            REAL NOT NULL,
    updated_at            REAL NOT NULL,
    submitted_at          REAL,
    approved_by           TEXT,
    approved_at           REAL,
    approval_reason       TEXT,
    rejected_by           TEXT,
    rejected_at           REAL,
    reject_reason         TEXT,
    executed_by           TEXT,
    executed_at           REAL,
    worker_task_id        TEXT,
    execution_status      TEXT,
    exec_result           TEXT NOT NULL DEFAULT '{}',
    exec_log_path         TEXT NOT NULL DEFAULT '',
    source_ticket_id      TEXT,
    untrusted             INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (source_ticket_id) REFERENCES ops_tickets(id) ON DELETE SET NULL
);

CREATE INDEX idx_ops_tickets_status
    ON ops_tickets(status, created_at);
CREATE INDEX idx_ops_tickets_creator
    ON ops_tickets(created_by, created_at);
CREATE INDEX idx_ops_tickets_kind
    ON ops_tickets(kind, status, created_at);
CREATE INDEX idx_ops_tickets_source
    ON ops_tickets(source_ticket_id);

-- 审批后冻结：命令、参数、节点、执行环境和审批快照不可再变更。
CREATE TRIGGER trg_ops_tickets_immutable
BEFORE UPDATE OF
    kind, command, target_node_id, target_node_name, target_node_address,
    exec_env, params, approval_snapshot, approval_hash, created_by, created_at
ON ops_tickets
WHEN OLD.status IN ('approved', 'executing', 'succeeded', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'ops ticket is immutable after approval');
END;

CREATE TABLE ops_ticket_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES ops_tickets(id) ON DELETE CASCADE
);

CREATE INDEX idx_ops_events_ticket
    ON ops_ticket_events(ticket_id, created_at);

-- 管理员批准的知识工单会落成一条 knowledge 记录。
CREATE TABLE ops_knowledge (
    id                 TEXT PRIMARY KEY,
    ticket_id          TEXT UNIQUE,
    title              TEXT NOT NULL,
    content            TEXT NOT NULL,
    kind               TEXT NOT NULL DEFAULT 'experience',
    tags               TEXT NOT NULL DEFAULT '[]',
    source_ticket_ids  TEXT NOT NULL DEFAULT '[]',
    content_hash       TEXT NOT NULL UNIQUE,
    created_by         TEXT NOT NULL DEFAULT '',
    approved_by        TEXT NOT NULL DEFAULT '',
    created_at         REAL NOT NULL,
    approved_at        REAL NOT NULL,
    updated_at         REAL NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES ops_tickets(id) ON DELETE SET NULL
);

CREATE INDEX idx_ops_knowledge_created
    ON ops_knowledge(created_at);
CREATE INDEX idx_ops_knowledge_kind
    ON ops_knowledge(kind, approved_at);

CREATE VIRTUAL TABLE ops_tickets_fts USING fts5(
    ticket_id UNINDEXED,
    title,
    request,
    command,
    ai_suggestion,
    admin_note,
    approval_reason,
    reject_reason,
    exec_summary,
    tokenize = 'trigram'
);

CREATE VIRTUAL TABLE ops_knowledge_fts USING fts5(
    knowledge_id UNINDEXED,
    title,
    content,
    tags,
    tokenize = 'trigram'
);
