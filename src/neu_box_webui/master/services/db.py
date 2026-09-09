"""统一数据库模块 — SQLite 持久化，供 Master 侧各模块使用。

特性:
  - WAL 模式，每次操作独立连接、用完即关，避免锁争用
  - 表: users, user_credentials, experiments, folders

块（block）模型:
  实验由多个 block 组成，按顺序排列:
    {"type": "text",  "content": "markdown 文本"}
    {"type": "task",  "task_id": "...", "node_id": "...",
     "command": "...", "log": { "status": "...", "result": {...} }}

用法:
    from neu_box_webui.master.services.db import Database
    db = Database.get_instance()
"""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from neu_box_webui.config import env_text, user_data_dir
from neu_box_webui.database.migrations import require_current_schema


MIGRATIONS_PACKAGE = "neu_box_webui.master.migrations"
REQUIRED_COLUMNS = {
    "users": (
        "id", "username", "password_hash", "role", "created_at",
        "is_active", "last_login_at",
    ),
    "user_credentials": (
        "id", "user_id", "node_name", "username", "created_at",
        "password", "updated_at",
    ),
    "experiments": (
        "id", "title", "blocks", "tags", "created_by", "folder_id",
        "created_at", "updated_at",
    ),
    "folders": ("id", "name", "parent_id", "created_at"),
    "task_submissions": (
        "id", "task_id", "node_id", "user_name", "command", "created_at",
    ),
}
REQUIRED_INDEXES = (
    "idx_users_username",
    "idx_uc_user",
    "idx_exp_created",
    "idx_exp_created_by",
    "idx_exp_folder",
    "idx_folder_parent",
    "idx_ts_user",
    "idx_ts_node",
)


def database_path() -> str:
    explicit = env_text("NEU_BOX_DB_PATH")
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    legacy_dir = env_text("db_dir")
    if legacy_dir:
        return os.path.abspath(os.path.join(os.path.expanduser(legacy_dir), "master.db"))
    return str(user_data_dir("master") / "master.db")


class Database:
    """SQLite 数据库单例 — 每次操作独立连接，用完即关。"""

    _instance = None

    def __init__(self, db_path: str = None):
        self._db_path = os.path.abspath(db_path or database_path())
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        require_current_schema(
            self._db_path,
            MIGRATIONS_PACKAGE,
            REQUIRED_COLUMNS,
            REQUIRED_INDEXES,
        )
        # WAL 是数据库级持久设置，只在单线程启动阶段配置一次。
        # 若在每个请求的新连接上重复执行，并发鉴权会争抢模式切换锁，
        # 最终占满整个 Waitress 线程池。
        with sqlite3.connect(self._db_path, timeout=5) as conn:
            conn.execute("PRAGMA journal_mode=WAL")

    @classmethod
    def get_instance(cls) -> 'Database':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @contextmanager
    def _conn(self):
        """每次操作创建独立连接，用完自动关闭。"""
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════
    # Experiments CRUD
    # ═══════════════════════════════════════════════════════════

    def create_experiment(self, title: str, blocks: list = None,
                          tags: list = None, created_by: str = '',
                          folder_id: str = None, exp_id: str = None) -> str:
        with self._conn() as conn:
            exp_id = exp_id or uuid.uuid4().hex[:12]
            now = time.time()
            conn.execute(
                'INSERT INTO experiments (id, title, blocks, tags, created_by, folder_id, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (exp_id, title,
                 json.dumps(blocks or [], ensure_ascii=False),
                 json.dumps(tags or [], ensure_ascii=False),
                 created_by, folder_id, now, now))
            conn.commit()
            return exp_id

    def update_experiment(self, exp_id: str, **fields) -> bool:
        with self._conn() as conn:
            allowed = {'title', 'blocks', 'tags', 'folder_id'}
            updates = {}
            for k in allowed:
                if k in fields and fields[k] is not None:
                    val = fields[k]
                    if k in ('blocks', 'tags') and isinstance(val, list):
                        val = json.dumps(val, ensure_ascii=False)
                    updates[k] = val
            if not updates:
                return False
            updates['updated_at'] = time.time()
            set_clause = ', '.join(f'{k}=?' for k in updates)
            values = list(updates.values()) + [exp_id]
            conn.execute(f'UPDATE experiments SET {set_clause} WHERE id=?', values)
            conn.commit()
            return True

    def delete_experiment(self, exp_id: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute('DELETE FROM experiments WHERE id=?', (exp_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_experiment(self, exp_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                'SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def list_experiments(self, search: str = '', tag: str = '',
                         created_by: str = '', folder_id: str = None,
                         limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            conditions = []
            params = []
            if search:
                conditions.append('(title LIKE ? OR tags LIKE ? OR blocks LIKE ?)')
                like = f'%{search}%'
                params.extend([like, like, like])
            if tag:
                conditions.append('tags LIKE ?')
                params.append(f'%{tag}%')
            if created_by:
                conditions.append('created_by = ?')
                params.append(created_by)
            if folder_id is not None:
                children = self._get_folder_descendants(folder_id)
                all_ids = [folder_id] + children
                placeholders = ','.join('?' for _ in all_ids)
                conditions.append(f'folder_id IN ({placeholders})')
                params.extend(all_ids)
            where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
            query = f'SELECT * FROM experiments {where} ORDER BY created_at DESC LIMIT ?'
            params.append(limit)
            return [self._row_to_dict(r) for r in conn.execute(query, params).fetchall()]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ('tags', 'blocks'):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = [] if key == 'tags' else []
        return d

    # ═══════════════════════════════════════════════════════════
    # Folders CRUD
    # ═══════════════════════════════════════════════════════════

    def _get_folder_descendants(self, folder_id: str) -> list:
        """递归获取某文件夹下所有子文件夹 ID。"""
        with self._conn() as conn:
            result = []
            stack = [folder_id]
            while stack:
                rows = conn.execute(
                    'SELECT id FROM folders WHERE parent_id IN ({})'.format(
                        ','.join('?' for _ in stack)),
                    stack).fetchall()
                stack = [r['id'] for r in rows]
                result.extend(stack)
            return result

    def create_folder(self, name: str, parent_id: str = None) -> str:
        with self._conn() as conn:
            fid = uuid.uuid4().hex[:8]
            now = time.time()
            conn.execute(
                'INSERT INTO folders (id, name, parent_id, created_at) VALUES (?, ?, ?, ?)',
                (fid, name, parent_id, now))
            conn.commit()
            return fid

    def get_folder_tree(self) -> list:
        """返回所有文件夹列表，每个含 id/name/parent_id/children/exp_count。"""
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT f.*, (SELECT COUNT(*) FROM experiments e '
                'WHERE e.folder_id = f.id) AS exp_count '
                'FROM folders f ORDER BY f.name ASC'
            ).fetchall()
        folders = [dict(r) for r in rows]
        node_map = {f['id']: {**f, 'children': []} for f in folders}
        tree = []
        for f in folders:
            node = node_map[f['id']]
            if f['parent_id'] and f['parent_id'] in node_map:
                node_map[f['parent_id']]['children'].append(node)
            else:
                tree.append(node)

        def _agg(n):
            total = n.get('exp_count', 0) or 0
            for c in n['children']:
                total += _agg(c)
            n['total_exp_count'] = total
            return total
        for n in tree:
            _agg(n)
        return tree

    def rename_folder(self, fid: str, name: str) -> bool:
        with self._conn() as conn:
            conn.execute('UPDATE folders SET name=? WHERE id=?', (name, fid))
            conn.commit()
            return True

    def move_folder(self, fid: str, new_parent_id: str = None) -> bool:
        """移动文件夹到新父节点（不可是自己的子孙节点）。"""
        if new_parent_id and new_parent_id in self._get_folder_descendants(fid):
            return False
        with self._conn() as conn:
            conn.execute('UPDATE folders SET parent_id=? WHERE id=?',
                         (new_parent_id, fid))
            conn.commit()
            return True

    def delete_folder(self, fid: str) -> bool:
        """删除文件夹：子文件夹上移，实验 folder_id 置空。"""
        with self._conn() as conn:
            folder = conn.execute('SELECT * FROM folders WHERE id=?', (fid,)).fetchone()
            if not folder:
                return False
            conn.execute('UPDATE folders SET parent_id=? WHERE parent_id=?',
                         (folder['parent_id'], fid))
            conn.execute('UPDATE experiments SET folder_id=? WHERE folder_id=?',
                         (folder['parent_id'], fid))
            conn.execute('DELETE FROM folders WHERE id=?', (fid,))
            conn.commit()
            return True

    # ═══════════════════════════════════════════════════════════
    # Users
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _hash_password(password: str) -> str:
        import bcrypt
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    @staticmethod
    def _check_password(password: str, password_hash: str) -> bool:
        import bcrypt
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

    def create_user(self, username: str, password: str, role: str = 'user') -> str:
        """创建用户，返回 user_id。用户名冲突返回 None。"""
        with self._conn() as conn:
            uid = uuid.uuid4().hex[:12]
            now = time.time()
            password_hash = self._hash_password(password)
            try:
                conn.execute(
                    'INSERT INTO users (id, username, password_hash, role, created_at) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (uid, username, password_hash, role, now))
                conn.commit()
                return uid
            except sqlite3.IntegrityError:
                return None

    def verify_user(self, username: str, password: str) -> dict | None:
        """验证用户名密码，成功返回用户字典，失败返回 None。"""
        with self._conn() as conn:
            row = conn.execute(
                'SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if not row:
                return None
            if not self._check_password(password, row['password_hash']):
                return None
            return dict(row)

    def get_user(self, user_id: str) -> dict | None:
        """通过 ID 获取用户。"""
        with self._conn() as conn:
            row = conn.execute(
                'SELECT id, username, role, created_at, is_active, last_login_at '
                'FROM users WHERE id=?', (user_id,)).fetchone()
            return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict | None:
        """通过用户名获取用户（不返回密码哈希）。"""
        with self._conn() as conn:
            row = conn.execute(
                'SELECT id, username, role, created_at, is_active, last_login_at '
                'FROM users WHERE username=?', (username,),
            ).fetchone()
            return dict(row) if row else None

    def list_users(self) -> list[dict]:
        """列出所有用户（不含密码哈希）。"""
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT id, username, role, created_at, is_active, last_login_at '
                'FROM users ORDER BY created_at'
            ).fetchall()
            return [dict(r) for r in rows]

    def update_last_login(self, user_id: str) -> None:
        """记录最近登录时间。"""
        with self._conn() as conn:
            conn.execute(
                'UPDATE users SET last_login_at=? WHERE id=?',
                (time.time(), user_id))
            conn.commit()

    def set_user_role(self, user_id: str, role: str) -> bool:
        if role not in ('user', 'admin'):
            return False
        with self._conn() as conn:
            cursor = conn.execute(
                'UPDATE users SET role=? WHERE id=?', (role, user_id))
            conn.commit()
            return cursor.rowcount > 0

    def set_user_active(self, user_id: str, active: bool) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                'UPDATE users SET is_active=? WHERE id=?',
                (1 if active else 0, user_id))
            conn.commit()
            return cursor.rowcount > 0

    def count_active_admins(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM users "
                "WHERE role='admin' AND is_active=1").fetchone()
            return int(row['n']) if row else 0

    def update_password(self, user_id: str, new_password: str) -> bool:
        """修改用户密码。"""
        with self._conn() as conn:
            password_hash = self._hash_password(new_password)
            cursor = conn.execute(
                'UPDATE users SET password_hash=? WHERE id=?',
                (password_hash, user_id))
            conn.commit()
            return cursor.rowcount > 0

    # ═══════════════════════════════════════════════════════════
    # User Credentials (per-node)
    # ═══════════════════════════════════════════════════════════

    def save_credential(self, user_id: str, node_name: str,
                        username: str, password: str | None = None) -> bool:
        """保存或更新用户对某节点的凭据。

        password 语义：
          - None        → 不改动已有密码
          - ""（空串）  → 清除已有密码
          - 非空字符串  → 替换为加密后的新密码
        """
        from neu_box_webui.master.services.crypto import encrypt_secret

        with self._conn() as conn:
            now = time.time()
            existing = conn.execute(
                'SELECT id, password FROM user_credentials '
                'WHERE user_id=? AND node_name=?',
                (user_id, node_name)).fetchone()
            if existing:
                if password is None:
                    conn.execute(
                        'UPDATE user_credentials '
                        'SET username=?, updated_at=? WHERE id=?',
                        (username, now, existing['id']))
                else:
                    token = encrypt_secret(password) if password else None
                    conn.execute(
                        'UPDATE user_credentials '
                        'SET username=?, password=?, updated_at=? WHERE id=?',
                        (username, token, now, existing['id']))
            else:
                cid = uuid.uuid4().hex[:12]
                token = encrypt_secret(password) if password else None
                conn.execute(
                    'INSERT INTO user_credentials '
                    '(id, user_id, node_name, username, password, '
                    ' created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (cid, user_id, node_name, username, token, now, now))
            conn.commit()
            return True

    def get_credential(self, user_id: str, node_name: str) -> dict | None:
        """获取单条凭据（含加密密码字段，内部使用）。"""
        with self._conn() as conn:
            row = conn.execute(
                'SELECT node_name, username, password, created_at, updated_at '
                'FROM user_credentials WHERE user_id=? AND node_name=?',
                (user_id, node_name)).fetchone()
            return dict(row) if row else None

    def get_credential_password(self, user_id: str, node_name: str) -> str | None:
        """解密返回某条凭据的密码；未设置或解密失败返回 None。"""
        from neu_box_webui.master.services.crypto import decrypt_secret

        cred = self.get_credential(user_id, node_name)
        if not cred or not cred.get('password'):
            return None
        return decrypt_secret(cred['password'])

    def get_credentials(self, user_id: str) -> list[dict]:
        """获取用户所有已存节点凭据（不含密码，仅 has_password 标志）。"""
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT node_name, username, created_at, updated_at, password '
                'FROM user_credentials WHERE user_id=? ORDER BY node_name',
                (user_id,)).fetchall()
            return [
                {
                    'node_name': r['node_name'],
                    'username': r['username'],
                    'created_at': r['created_at'],
                    'updated_at': r['updated_at'],
                    'has_password': bool(r['password']),
                }
                for r in rows
            ]

    def delete_credential(self, user_id: str, node_name: str) -> bool:
        """删除用户对某节点的凭据。"""
        with self._conn() as conn:
            cursor = conn.execute(
                'DELETE FROM user_credentials WHERE user_id=? AND node_name=?',
                (user_id, node_name))
            conn.commit()
            return cursor.rowcount > 0

    # ═══════════════════════════════════════════════════════
    # Task Submissions（提交登记：我的任务历史 / dashboard）
    # ═══════════════════════════════════════════════════════

    def record_submission(self, task_id: str, node_id: str,
                          user_name: str, command: str = '') -> str:
        """登记一次任务提交，返回记录 id。"""
        with self._conn() as conn:
            sub_id = uuid.uuid4().hex[:12]
            conn.execute(
                'INSERT INTO task_submissions '
                '(id, task_id, node_id, user_name, command, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (sub_id, task_id, node_id, user_name, command, time.time()))
            conn.commit()
            return sub_id

    def list_submissions(self, user_name: str, limit: int = 200) -> list[dict]:
        """按用户列出提交历史（新→旧）。"""
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT id, task_id, node_id, user_name, command, created_at '
                'FROM task_submissions WHERE user_name=? '
                'ORDER BY created_at DESC LIMIT ?',
                (user_name, limit)).fetchall()
            return [dict(r) for r in rows]
