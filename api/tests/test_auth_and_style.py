"""阶段 2：鉴权 + 以用户身份读配置。

**每条「拒绝」都配了允许控制**（口径 F11）：一个各方面正确的 token 必须 200，
否则那些 401 可能只是「什么都进不来」。
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings
from api.auth import TokenVerifier
from api.supabase import SupabaseClient

from conftest import AUDIENCE, ISSUER, USER_A, USER_B, KeyPair, StaticJwks
from fake_db import FakeRest, row

SETTINGS = Settings(
    supabase_url="https://example.supabase.co",
    supabase_publishable_key="sb_publishable_test",
    anthropic_api_key=None,
)


def build(jwks: StaticJwks, rest: FakeRest) -> TestClient:
    app = create_app(
        settings=SETTINGS,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(SETTINGS.rest_url, SETTINGS.supabase_publishable_key, transport=rest),
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def rest(keypair: KeyPair) -> FakeRest:
    return FakeRest({USER_A: [row(USER_A)], USER_B: [row(USER_B, detail_level="P3")]})


class TestAllowControl:
    """**先立允许控制。** 没有它，下面所有 401 都不携带信息。"""

    def test_valid_token_reads_own_row(self, keypair, jwks, rest):
        c = build(jwks, rest)
        r = c.get("/me/style", headers={"Authorization": f"Bearer {keypair.sign(sub=USER_A)}"})
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == USER_A

    def test_a_and_b_see_different_rows(self, keypair, jwks, rest):
        """两个账号读到的是**不同的行**。

        少了它，一个「永远返回同一行」的实现也能让上一条全绿。
        """
        c = build(jwks, rest)
        a = c.get("/me/style", headers={"Authorization": f"Bearer {keypair.sign(sub=USER_A)}"}).json()
        b = c.get(
            "/me/style",
            headers={"Authorization": f"Bearer {keypair.sign(sub=USER_B, email='b@example.com')}"},
        ).json()
        assert a["user_id"] == USER_A and b["user_id"] == USER_B
        assert a["detail_level"] == "P0" and b["detail_level"] == "P3"

    def test_health_needs_no_token(self, jwks, rest):
        assert build(jwks, rest).get("/health").status_code == 200


class TestRejects:
    def test_no_header(self, jwks, rest):
        r = build(jwks, rest).get("/me/style")
        assert r.status_code == 401 and "Authorization" in r.json()["detail"]

    def test_wrong_scheme(self, keypair, jwks, rest):
        r = build(jwks, rest).get("/me/style", headers={"Authorization": f"Token {keypair.sign()}"})
        assert r.status_code == 401 and "Bearer" in r.json()["detail"]

    def test_garbage_token(self, jwks, rest):
        r = build(jwks, rest).get("/me/style", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401

    def test_signed_by_another_key_is_rejected(self, other_keypair, jwks, rest):
        """**这条证明验签真的在验。**

        用另一把私钥签，`kid` 故意相同——只有真的验签名才分得出来。
        少了它，一个只解码不验签的实现会让上面的允许控制照样全绿。
        """
        r = build(jwks, rest).get(
            "/me/style", headers={"Authorization": f"Bearer {other_keypair.sign(sub=USER_A)}"}
        )
        assert r.status_code == 401 and "验签失败" in r.json()["detail"]

    def test_expired(self, keypair, jwks, rest):
        now = int(time.time())
        tok = keypair.sign(iat=now - 7200, exp=now - 3600)
        r = build(jwks, rest).get("/me/style", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401 and "过期" in r.json()["detail"]

    def test_wrong_audience(self, keypair, jwks, rest):
        r = build(jwks, rest).get(
            "/me/style", headers={"Authorization": f"Bearer {keypair.sign(aud='anon')}"}
        )
        assert r.status_code == 401

    def test_wrong_issuer(self, keypair, jwks, rest):
        r = build(jwks, rest).get(
            "/me/style",
            headers={"Authorization": f"Bearer {keypair.sign(iss='https://evil.example/auth/v1')}"},
        )
        assert r.status_code == 401

    def test_algorithm_confusion_none(self, jwks, rest):
        """`alg: none` 必须被拒。算法写死在验证参数里，不从 token 头部读。"""
        tok = jwt.encode({"sub": USER_A, "aud": AUDIENCE, "iss": ISSUER, "exp": 9999999999},
                         key=None, algorithm="none", headers={"kid": "test-kid-1"})
        r = build(jwks, rest).get("/me/style", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401

    def test_unknown_kid(self, keypair, jwks, rest):
        tok = jwt.encode({"sub": USER_A, "aud": AUDIENCE, "iss": ISSUER, "exp": 9999999999},
                         keypair.private_pem, algorithm="ES256", headers={"kid": "someone-else"})
        r = build(jwks, rest).get("/me/style", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401 and "kid" in r.json()["detail"]


class TestFailureModesAreDistinct:
    """**「你没资格」与「我这边坏了」必须分开。**

    把取不到 JWKS 报成 401，用户会去重新登录而问题在服务端；
    反过来把过期 token 报成 503，用户会一直等。
    """

    def test_jwks_unavailable_is_503_not_401_and_not_200(self, keypair, jwks, rest):
        jwks.fail = True
        r = build(jwks, rest).get("/me/style", headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 503, f"取不到公钥时返回了 {r.status_code}"

    def test_zero_rows_is_bad_state_not_empty_state(self, keypair, jwks):
        """读到 0 行是坏状态（口径 F09），不是「用户还没配置」。"""
        c = build(StaticJwks(keypair), FakeRest({}))
        r = c.get("/me/style", headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 500 and "F09" in r.json()["detail"]


class TestRlsIsEnforcedByTheDatabase:
    """服务**不替数据库做行级过滤**——它把用户的 token 原样转过去。"""

    def test_user_token_is_forwarded_to_postgrest(self, keypair, jwks, rest):
        tok = keypair.sign(sub=USER_A)
        build(jwks, rest).get("/me/style", headers={"Authorization": f"Bearer {tok}"})
        assert rest.seen, "根本没打到数据库"
        _, headers = rest.seen[-1]
        assert headers["Authorization"] == f"Bearer {tok}", "转给 PostgREST 的不是用户的 token"
        assert headers["apikey"] == "sb_publishable_test"

    def test_service_never_holds_a_service_role_key(self):
        """配了 secret / service_role key 就**起不来**（口径 F40）。"""
        from api.settings import load_settings

        for bad in ("sb_secret_" + "x" * 24, "eyJ.service_role.x"):
            with pytest.raises(RuntimeError, match="service_role|secret"):
                load_settings({"SUPABASE_URL": "https://x.supabase.co",
                               "SUPABASE_PUBLISHABLE_KEY": bad})

    def test_missing_env_refuses_to_start(self):
        from api.settings import load_settings

        with pytest.raises(RuntimeError, match="缺少环境变量"):
            load_settings({"SUPABASE_URL": "https://x.supabase.co"})
