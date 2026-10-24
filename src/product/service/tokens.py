"""邀请、会话与 CSRF 的秘密（OWASP 会话管理的做法：高熵随机值、服务端只存摘要、比较用定长时间）。

* 邀请秘密与会话秘密：32 字节密码学随机值（``secrets.token_urlsafe(32)``，43 个字符），服务端只保存 SHA-256 摘要；
* CSRF token：由服务端密钥对会话秘密做 HMAC 得到（会话绑定，不另存）；服务端密钥在数据目录里生成一次，不进 git；
* 不透明编号（``pr-`` / ``draft-`` / ``intent-`` / ``sup-``）：随机，不含任何用户内容。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

SECRET_BYTES = 32


def new_secret() -> str:
    return secrets.token_urlsafe(SECRET_BYTES)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def opaque(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def support_ref() -> str:
    return opaque("sup")


def server_key(path: Path) -> bytes:
    """服务端 HMAC 密钥：第一次启动时生成（32 字节随机值），之后读回；文件只在数据目录里。"""
    if path.is_file():
        data = path.read_bytes()
        if len(data) >= SECRET_BYTES:
            return data
    path.parent.mkdir(parents=True, exist_ok=True)
    data = secrets.token_bytes(SECRET_BYTES)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return data


def csrf_for(key: bytes, session_secret: str) -> str:
    mac = hmac.new(key, b"csrf|" + session_secret.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")
