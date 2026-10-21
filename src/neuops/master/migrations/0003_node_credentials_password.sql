-- 节点凭据增强：节点上的用户名 + 密码（Fernet 加密存储，仅本人可见）
-- 背景：WebUI 用户名不一定等于节点 OS 用户名；用户需要为每个节点维护
-- 自己的 OS 账号（提交任务时作为 worker 侧 user_id 归属显示，密码供
-- 用户登录该节点参考）。

ALTER TABLE user_credentials ADD COLUMN password TEXT;
ALTER TABLE user_credentials ADD COLUMN updated_at REAL;
