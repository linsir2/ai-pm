"""密码学唯一落点：全仓只有这里碰密码哈希（`grep -rn argon2 src/` 必须只有本文件）。"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()  # 默认即 RFC 9106 LOW_MEMORY，已超 OWASP 下限，不自创参数


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash: str, password: str) -> bool:
    try:
        return _hasher.verify(hash, password)
    except VerifyMismatchError:
        return False
