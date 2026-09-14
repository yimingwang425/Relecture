"""共用夹具：一把**本地生成的 EC P-256 私钥** + 由它签出的真 token。

## 为什么不是打桩

打掉验签的测试只能证明「调用了 verify」，证明不了 verify 会拒绝什么
（通则七 #7：这个对象被哪条断言碰过？）。这里走的是 `jwt.decode` 那条真实路径：
私钥在测试里，公钥进注入的 JWKS，签名是真的算出来的。

所以下面那些「必须 401」的用例，拒的是真的签名/过期/受众检查——
而**允许控制**（一个各方面都正确的 token 必须 200）证明那些 401 不是
「什么都进不来」。
"""

from __future__ import annotations

import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import PyJWKSet
from jwt.algorithms import ECAlgorithm

ISSUER = "https://example.supabase.co/auth/v1"
AUDIENCE = "authenticated"
USER_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


class KeyPair:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self._priv = ec.generate_private_key(ec.SECP256R1())

    @property
    def private_pem(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self._priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def jwk(self) -> dict[str, Any]:
        d = json.loads(ECAlgorithm.to_jwk(self._priv.public_key()))
        d.update({"kid": self.kid, "alg": "ES256", "use": "sig"})
        return d

    def sign(self, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": USER_A,
            "aud": AUDIENCE,
            "iss": ISSUER,
            "iat": now,
            "exp": now + 3600,
            "email": "a@example.com",
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_pem, algorithm="ES256", headers={"kid": self.kid})


class StaticJwks:
    """注入用的公钥源。`fail` 打开时模拟取不到 JWKS。"""

    def __init__(self, *pairs: KeyPair) -> None:
        self._set = PyJWKSet.from_dict({"keys": [p.jwk() for p in pairs]})
        self.fail = False

    def get(self) -> PyJWKSet:
        if self.fail:
            from api.auth import AuthError

            raise AuthError("取不到 Supabase 公钥（JWKS）：模拟失败", status=503)
        return self._set


@pytest.fixture(autouse=True)
def _no_real_model_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """**测试里任何一次真模型调用都必须失败，而且是离线失败。**

    内测前-P1 起 `/explain` 默认就接真的兜底判断（口径 F70，不加开关）。
    没注入 `gap_checker_factory` 的测试会走到 `build_model_gap_checker()` ——
    本机环境里若恰好有 `ANTHROPIC_API_KEY`，那就是一次真的 Haiku 调用、真的钱，
    而且测试结果取决于网络。拿掉它之后 `AnthropicModel._ensure_client` 当场抛，
    被 `GuardedGapChecker` 接住成 `implementation="error"`：离线、确定、不花钱。
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def keypair() -> KeyPair:
    return KeyPair("test-kid-1")


@pytest.fixture
def other_keypair() -> KeyPair:
    """**另一把**私钥。用它签出的 token 必须被拒——这条证明验签真的在验。"""
    return KeyPair("test-kid-1")  # 同一个 kid，不同的密钥：只有验签能分辨


@pytest.fixture
def jwks(keypair: KeyPair) -> StaticJwks:
    return StaticJwks(keypair)
