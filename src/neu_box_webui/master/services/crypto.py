"""节点凭据密码的对称加密（Fernet）。

密钥从 SECRET_KEY 派生（SHA-256 → urlsafe-base64），与 Flask session
使用同一配置来源；SECRET_KEY 变化/缺失时旧凭据不可解密（降级为
"未设置密码"，不影响其他功能）。
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from neu_box_webui.config import env_text

logger = logging.getLogger('master.crypto')

_KEY_PURPOSE = b'neu-box-node-credentials-v1'
_fernet: Fernet | None = None


def _get_fernet() -> Fernet | None:
    global _fernet
    if _fernet is None:
        secret = env_text('SECRET_KEY')
        if not secret:
            logger.warning('未设置 SECRET_KEY：节点凭据密码无法加密存储')
            return None
        key = base64.urlsafe_b64encode(
            hashlib.sha256(_KEY_PURPOSE + secret.encode('utf-8')).digest())
        _fernet = Fernet(key)
    return _fernet


def encrypt_secret(plaintext: str) -> str | None:
    """加密明文；无可用密钥时返回 None（调用方按未设置处理）。"""
    fernet = _get_fernet()
    if fernet is None:
        return None
    return fernet.encrypt(plaintext.encode('utf-8')).decode('ascii')


def decrypt_secret(token: str) -> str | None:
    """解密；失败（密钥变化/数据损坏）返回 None。"""
    fernet = _get_fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(token.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        logger.warning('节点凭据密码解密失败: %s', exc)
        return None
