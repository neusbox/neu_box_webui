-- 用户邮箱 + 邮箱验证码登录。
--
-- email 可为空以兼容旧用户；自助注册与管理端新建用户时建议填写。
-- 邮箱验证码只保存 HMAC/SHA-256 摘要，不保存明文。

ALTER TABLE users ADD COLUMN email TEXT;
ALTER TABLE users ADD COLUMN email_verified_at REAL;

CREATE UNIQUE INDEX idx_users_email
    ON users(email)
    WHERE email IS NOT NULL AND email != '';

CREATE TABLE email_login_codes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    email       TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_email_codes_user
    ON email_login_codes(user_id, created_at);
CREATE INDEX idx_email_codes_email
    ON email_login_codes(email, created_at);
