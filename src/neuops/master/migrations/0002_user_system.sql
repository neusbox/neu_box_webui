-- 用户系统完善：启用/禁用、最近登录、任务提交登记（"我的队列"视图基础）

ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN last_login_at REAL;

-- 任务提交登记表：master 侧记录每次经 WebUI 提交的任务。
-- 不替代 worker 队列（worker 仍是任务状态的唯一事实来源），
-- 用途：我的任务历史（任务从 worker 队列消失后仍可查）、dashboard 统计。
CREATE TABLE task_submissions (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    user_name  TEXT NOT NULL,
    command    TEXT,
    created_at REAL
);

CREATE INDEX idx_ts_user ON task_submissions(user_name, created_at);
CREATE INDEX idx_ts_node ON task_submissions(node_id, created_at);
