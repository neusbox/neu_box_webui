"""Master 用户认证 — 登录/登出/凭据管理。

会话:
  - 登录后 session['user_id'] 标识当前用户
  - @login_required 装饰器拦截未登录请求

节点凭据:
  - 每个用户可为不同节点保存命令任务 username
  - 前端选中节点时自动填入已存凭据
"""

import functools
import logging
import re

from flask import Blueprint, request, session

from neu_box_webui.config import env_text
from neu_box_webui.master.services.db import Database

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger('master.auth')

# 注册开关：默认开放（实验室集群内部使用），设 NEU_BOX_ALLOW_REGISTRATION=0 关闭
def _registration_allowed() -> bool:
    return env_text('NEU_BOX_ALLOW_REGISTRATION', '1') in ('1', 'true', 'yes')

_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_.-]{2,32}$')
# 节点 OS 用户名可以较短（如 "al"、"li"）；不允许单字符
_NODE_USER_RE = re.compile(r'^[A-Za-z0-9_.-]{2,32}$')


def __getattr__(name: str):
    """延迟获取 Database 单例，避免导入期绑定导致测试/迁移后拿到旧实例。"""
    if name == 'db':
        return Database.get_instance()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def _db() -> "Database":
    """每次调用取当前单例（延迟绑定，便于测试替换/迁移）。"""
    return Database.get_instance()



def login_required(f):
    """装饰器：要求已登录且账号启用，否则返回 401。"""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        uid = session.get('user_id')
        if not uid:
            return {'error': '请先登录'}, 401
        user = _db().get_user(uid)
        if not user or not user.get('is_active', 1):
            session.pop('user_id', None)
            return {'error': '请先登录（账号可能已被禁用）'}, 401
        return f(*args, **kwargs)
    return wrapper


def get_current_user() -> dict | None:
    """获取当前登录用户，未登录返回 None。"""
    uid = session.get('user_id')
    if not uid:
        return None
    return _db().get_user(uid)


# ═══════════════════════════════════════════════════════════════
# 登录 / 登出
# ═══════════════════════════════════════════════════════════════

@auth_bp.route('/login', methods=['POST'])
def login():
    """登录。

    请求体: { "username": "...", "password": "..." }
    成功: { "user": { "id", "username", "role" }, "message": "..." }
    失败: { "error": "..." }, 401
    """
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return {'error': '用户名和密码不能为空'}, 400

    user = _db().verify_user(username, password)
    if not user:
        logger.warning('登录失败: %s', username)
        return {'error': '用户名或密码错误'}, 401
    if not user.get('is_active', 1):
        logger.warning('已禁用账号尝试登录: %s', username)
        return {'error': '账号已被禁用，请联系管理员'}, 403

    session.permanent = True
    session['user_id'] = user['id']
    _db().update_last_login(user['id'])
    logger.info('用户登录: %s (%s)', username, user['role'])

    return {
        'user': {
            'id': user['id'],
            'username': user['username'],
            'role': user['role'],
        },
        'message': '登录成功',
    }, 200


@auth_bp.route('/register', methods=['GET'])
def register_status():
    """注册开关状态（前端登录页据此显示/隐藏注册入口）。"""
    return {'allowed': _registration_allowed()}, 200


@auth_bp.route('/register', methods=['POST'])
def register():
    """自助注册（普通用户角色）。

    请求体: { "username": "...", "password": "..." }
    成功后自动登录。开关关闭时 403。
    """
    if not _registration_allowed():
        return {'error': '注册已关闭，请联系管理员创建账号'}, 403

    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not _USERNAME_RE.match(username):
        return {'error': '用户名需 2-32 位字母、数字、_、. 或 -'}, 400
    if len(password) < 4 or len(password) > 128:
        return {'error': '密码长度需 4-128 位'}, 400
    if _db().get_user_by_username(username):
        return {'error': '用户名已被占用'}, 409

    user_id = _db().create_user(username, password, role='user')
    session.permanent = True
    session['user_id'] = user_id
    _db().update_last_login(user_id)
    logger.info('新用户注册: %s', username)

    return {
        'user': {'id': user_id, 'username': username, 'role': 'user'},
        'message': '注册成功',
    }, 201


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """登出，清空 session。"""
    uid = session.pop('user_id', None)
    if uid:
        logger.info('用户登出: %s', uid)
    return {'message': '已登出'}, 200


@auth_bp.route('/me', methods=['GET'])
@login_required
def me():
    """返回当前登录用户信息。"""
    user = get_current_user()
    if not user:
        return {'error': '登录已过期'}, 401
    return {'user': user}, 200


# ═══════════════════════════════════════════════════════════════
# 修改密码
# ═══════════════════════════════════════════════════════════════

@auth_bp.route('/password', methods=['PUT'])
@login_required
def change_password():
    """修改当前用户密码。

    请求体: { "old_password": "...", "new_password": "..." }
    """
    data = request.get_json(silent=True) or {}
    old_password = data.get('old_password') or ''
    new_password = data.get('new_password') or ''

    if not old_password or not new_password:
        return {'error': '旧密码和新密码不能为空'}, 400
    if len(new_password) < 4:
        return {'error': '新密码至少 4 位'}, 400
    if old_password == new_password:
        return {'error': '新密码不能与旧密码相同'}, 400

    user = get_current_user()
    if not user:
        return {'error': '登录已过期'}, 401

    # 验证旧密码
    verified = _db().verify_user(user['username'], old_password)
    if not verified:
        return {'error': '旧密码不正确'}, 403

    _db().update_password(user['id'], new_password)
    logger.info('用户 %s 修改了密码', user['username'])
    return {'message': '密码已修改，请重新登录'}, 200


# ═══════════════════════════════════════════════════════════════
# 节点凭据管理（节点上的 OS 用户名 + 密码）
#
# 语义：每个用户为每个节点维护自己的 OS 账号。
#   - username: 提交任务到该节点时作为 worker 侧 user_id 归属
#               （未设置时用 WebUI 用户名）
#   - password: Fernet 加密存 master 侧，仅本人可见（供登录节点参考）
# ═══════════════════════════════════════════════════════════════

@auth_bp.route('/credentials', methods=['GET'])
@login_required
def list_credentials():
    """获取当前用户所有已存节点凭据（不含密码，仅 has_password 标志）。"""
    user_id = session['user_id']
    creds = _db().get_credentials(user_id)
    return {'credentials': creds}, 200


@auth_bp.route('/credentials/<node_name>', methods=['PUT'])
@login_required
def save_credential(node_name: str):
    """保存或更新一条节点凭据。

    请求体: { "username": "...", "password": "...?" }
    password 缺省 = 不改密码；空串 = 清除密码。
    """
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    if not username:
        return {'error': '节点用户名不能为空'}, 400
    if not _NODE_USER_RE.match(username):
        return {'error': '节点用户名需 2-32 位字母、数字、_、. 或 -'}, 400

    password = data.get('password', None)
    if password is not None:
        password = str(password)
        if len(password) > 128:
            return {'error': '密码长度需 ≤ 128 位'}, 400

    user_id = session['user_id']
    _db().save_credential(user_id, node_name, username, password)
    logger.info('用户 %s 更新节点凭据: %s → %s (password=%s)',
                user_id, node_name, username,
                'unchanged' if password is None else ('cleared' if password == '' else 'set'))
    return {'message': f'节点 "{node_name}" 凭据已保存'}, 200


@auth_bp.route('/credentials/<node_name>/password', methods=['GET'])
@login_required
def reveal_credential_password(node_name: str):
    """查看本人某节点凭据的密码（明文返回，仅本人）。"""
    user_id = session['user_id']
    if not _db().get_credential(user_id, node_name):
        return {'error': '该节点的凭据不存在'}, 404
    password = _db().get_credential_password(user_id, node_name)
    if password is None:
        return {'password': None}, 200
    return {'password': password}, 200


@auth_bp.route('/credentials/<node_name>', methods=['DELETE'])
@login_required
def delete_credential(node_name: str):
    """删除一条节点凭据。"""
    user_id = session['user_id']
    ok = _db().delete_credential(user_id, node_name)
    if not ok:
        return {'error': '凭据不存在'}, 404
    logger.info('用户 %s 删除节点凭据: %s', user_id, node_name)
    return {'message': f'节点 "{node_name}" 凭据已删除'}, 200
