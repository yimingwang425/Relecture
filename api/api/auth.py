"""Supabase JWT 验签。

## 为什么自己验，而不是每次去问 Supabase

两条路都成立：① 把 token 转给 `/auth/v1/user` 让 Supabase 告诉我们是谁；
② 本地验签。取 ②，因为**每一次讲解请求都要鉴权，而生成本身就要几十秒**——
再加一次往返不是性能问题，是多一个会挂的依赖：Supabase auth 抖一下，
连「你是谁」都答不出来，而那一刻数据库其实是好的。

⚠️ **本地验签不替代 RLS。** 验签只回答「这个 token 是真的、没过期」；
「这个人能读哪些行」由数据库执行（见 `supabase.py`：所有请求带着**用户自己的
JWT** 打给 PostgREST）。两层，与迁移里 grant + policy 的两层是同一个做法——
任一层被误改，另一层还在。

## 算法是 ES256，不是 HS256（2026-09-11 实测）

探过这个项目的 JWKS 端点，返回一把 `alg: ES256` 的 EC P-256 公钥（带 `kid`）。
所以**服务端不需要任何共享密钥**就能验签——这正是非对称的意义，
也让「服务端泄露 = 能伪造任意用户」这条路不存在。

`alg` **写死在验证参数里**，不从 token 头部读：读 token 说自己用什么算法，
就是把选择权交给攻击者（`alg: none` 与 HS256-用公钥当密钥两个经典漏洞都从这里来）。

## 拿不到 JWKS 时**拒绝，不放行**

跳过了却返回 200 的鉴权，与它要防的失败是同一类东西（同 F15 的 `--allow-offline`：
跳过了却退出 0 的检查不是检查）。所以取不到公钥就是 503，不是「先放进来再说」。

决策日期：2026-09-11
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from jwt import PyJWKSet

__all__ = ["AuthError", "Principal", "JwksSource", "HttpJwks", "TokenVerifier", "bearer_token"]

#: 公钥缓存时长。拿不到新的**不会**沿用过期缓存——见 `CachingJwks.get`。
JWKS_TTL_SECONDS = 600


class AuthError(Exception):
    """鉴权失败。`status` 区分「你没资格」(401) 与「我这边坏了」(503)。

    分开是必需的：把取不到 JWKS 报成 401，用户会去重新登录，
    而问题在服务端；反过来把过期 token 报成 503，用户会一直等。
    """

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class Principal:
    """验过签的调用者。`user_id` 就是 `auth.users.id`，也就是 RLS 里的 `auth.uid()`。"""

    user_id: str
    email: str | None
    #: 原始 token。**要原样带给 PostgREST**——RLS 由数据库执行，不由这里转述。
    token: str


class JwksSource(Protocol):
    def get(self) -> PyJWKSet: ...


class HttpJwks:
    """从 Supabase 取公钥集，带 TTL 缓存。

    **拿不到就抛，不沿用过期缓存**：一把被轮换掉的公钥仍然能验通旧 token，
    而轮换的理由可能正是旧私钥泄露了。宁可 503。
    """

    def __init__(self, url: str, ttl: int = JWKS_TTL_SECONDS, client: Any | None = None) -> None:
        self._url = url
        self._ttl = ttl
        self._client = client
        self._lock = threading.Lock()
        self._cached: PyJWKSet | None = None
        self._fetched_at = 0.0

    def _fetch(self) -> PyJWKSet:
        import httpx

        client = self._client
        try:
            if client is None:
                resp = httpx.get(self._url, timeout=5.0)
            else:
                resp = client.get(self._url, timeout=5.0)
            resp.raise_for_status()
            return PyJWKSet.from_dict(resp.json())
        except Exception as exc:  # noqa: BLE001 —— 网络/格式都归一成同一种处置
            raise AuthError(f"取不到 Supabase 公钥（JWKS）：{exc}", status=503) from exc

    def get(self) -> PyJWKSet:
        with self._lock:
            fresh = self._cached is not None and (time.monotonic() - self._fetched_at) < self._ttl
            if fresh:
                assert self._cached is not None
                return self._cached
            keys = self._fetch()
            self._cached, self._fetched_at = keys, time.monotonic()
            return keys


class TokenVerifier:
    """验一个 bearer token，返回 `Principal`。

    `jwks` 可注入，所以断言里能用一把本地生成的 EC 私钥签出真 token 来验——
    **验的是真的签名验证路径**，不是一个被打桩掉的替身（通则七 #7）。
    """

    def __init__(self, jwks: JwksSource, issuer: str, audience: str = "authenticated") -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> Principal:
        if not token:
            raise AuthError("缺少 Authorization: Bearer <token>")

        keys = self._jwks.get()  # 503 由这里抛出，不被下面的 except 吞掉
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError(f"token 格式不合法：{exc}") from exc

        kid = header.get("kid")
        key = next((k for k in keys.keys if k.key_id == kid), None)
        if key is None:
            raise AuthError(f"token 的 kid={kid!r} 不在 Supabase 的公钥集里")

        try:
            claims = jwt.decode(
                token,
                key.key,
                # **算法写死**，不读 token 头部说自己用了什么。
                algorithms=["ES256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token 已过期，请重新登录") from exc
        except jwt.PyJWTError as exc:
            raise AuthError(f"token 验签失败：{exc}") from exc

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise AuthError("token 里没有 sub")
        email = claims.get("email")
        return Principal(user_id=sub, email=email if isinstance(email, str) else None, token=token)


def bearer_token(authorization: str | None) -> str:
    """从 `Authorization` 头里取 token。**只认 Bearer**。"""
    if not authorization:
        raise AuthError("缺少 Authorization 头")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("Authorization 头必须是 `Bearer <token>` 形式")
    return parts[1].strip()
