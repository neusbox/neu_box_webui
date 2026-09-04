"""管理员 API — 用户管理（仅 admin 可访问）。"""

import logging
import re

from flask import Blueprint, request

from neu_box_webui.master.api.auth import get_current_user
from neu_box_webui.master.api.permissions import require_admin
from neu_box_webui.master.services.db import Database

admin_bp = Blueprint('admin', __name__)
logger = logging.getLogger('master.admin')


def __getattr__(name: str):
    """延迟获取 Database 单例，避免导入期绑定导致测试/迁移后拿到旧实例。"""
    if name == 'db':
        return Database.get_instance()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def _db() -> "Database":
    """每次调用取当前单例（延迟绑定，便于测试替换/迁移）。"""
    return Database.get_instance()


_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_.-]{2,32}$')


def _self_user_id() -> str | None:
    user = get_current_user()
    return user['id'] if user else None


@admin_bp.route('/users', methods=['GET'])
@require_admin
def list_users():
    """用户列表（不含密码哈希）。"""
    return {'users': _db().list_users()}, 200


@admin_bp.route('/users', methods=['POST'])
@require_admin
def create_user():
    """创建用户。

    请求体: { "username": "...", "password": "...", "role": "user|admin" }
    """
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    role = (data.get('role') or 'user').strip()

    if not _USERNAME_RE.match(username):
        return {'error': '用户名需为 2-32 位字母/数字/_.- '}, 400
    if len(password) < 4:
        return {'error': '密码至少 4 位'}, 400
    if role not in ('user', 'admin'):
        return {'error': '角色只能是 user 或 admin'}, 400

    uid = _db().create_user(username, password, role=role)
    if not uid:
        return {'error': f'用户名 "{username}" 已存在'}, 409
    logger.info('管理员创建用户: %s (%s)', username, role)
    return {'id': uid, 'message': f'用户 "{username}" 已创建'}, 201


@admin_bp.route('/users/<user_id>', methods=['PUT'])
@require_admin
def update_user(user_id: str):
    """修改用户角色 / 启用状态。

    请求体: { "role"?: "user|admin", "is_active"?: true|false }

    保护规则:
      - 不能修改自己的角色或禁用自己
      - 不能禁用/降权最后一个启用的 admin
    """
    user = _db().get_user(user_id)
    if not user:
        return {'error': '用户不存在'}, 404

    data = request.get_json(silent=True) or {}
    self_id = _self_user_id()
    is_self = user_id == self_id

    new_role = data.get('role')
    new_active = data.get('is_active')

    if is_self and (new_role is not None or new_active is False):
        return {'error': '不能修改自己的角色或禁用自己'}, 400

    if user.get('role') == 'admin' and user.get('is_active', 1):
        # 该用户是当前启用的 admin 时，检查降权/禁用是否会使 admin 清零
        demoting = (new_role is not None and new_role != 'admin') or new_active is False
        if demoting and _db().count_active_admins() <= 1 and not is_self:
            return {'error': '不能移除最后一个启用的管理员'}, 400

    if new_role is not None:
        if new_role not in ('user', 'admin'):
            return {'error': '角色只能是 user 或 admin'}, 400
        _db().set_user_role(user_id, new_role)
    if new_active is not None:
        _db().set_user_active(user_id, bool(new_active))

    logger.info('管理员更新用户 %s: role=%s active=%s',
                user['username'], new_role, new_active)
    return {'message': '已更新'}, 200


@admin_bp.route('/users/<user_id>/password', methods=['POST'])
@require_admin
def reset_user_password(user_id: str):
    """重置用户密码。

    请求体: { "password": "..." }
    """
    user = _db().get_user(user_id)
    if not user:
        return {'error': '用户不存在'}, 404

    data = request.get_json(silent=True) or {}
    password = data.get('password') or ''
    if len(password) < 4:
        return {'error': '密码至少 4 位'}, 400

    _db().update_password(user_id, password)
    logger.info('管理员重置用户密码: %s', user['username'])
    return {'message': f'用户 "{user["username"]}" 密码已重置'}, 200
