"""权限装饰器 — login_required 见 auth.py，admin_required 在此。"""

from __future__ import annotations

import functools

from flask import session

from neu_box_webui.master.services.db import Database


def require_admin(f):
    """装饰器：要求已登录且 role == admin，否则返回 401/403。"""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return {'error': '请先登录'}, 401
        user = Database.get_instance().get_user(session['user_id'])
        if not user or user.get('role') != 'admin':
            return {'error': '需要管理员权限'}, 403
        return f(*args, **kwargs)
    return wrapper
