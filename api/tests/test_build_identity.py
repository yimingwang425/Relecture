"""**这个服务在跑哪个构建**（口径 F17），以及**未知请求字段一律拒**。

## 这一组是被一次真实的误读逼出来的（2026-09-12）

Render 第一次部署后，验收脚本的第 4 节报了两件事：崩在 `KeyError: 'retrieval'`，
而 `/explain` 的正文在讲**布隆过滤器**（`f1_samples/synthetic_deck.md`，阶段 3 的替身语料）。

真实原因：**Render 建的是当时的 HEAD `a0d1712`，阶段 5–6 的改动全都没提交。**
那个构建的 `ExplainRequest` 没有 `course_id`，而 pydantic 的 `extra` 默认是
`ignore` —— 那个字段**被静默丢掉**，检索走替身，返回 **200**。

**8 条断言在一个旧构建上通过了**，因为没有一条问过「你是哪个构建」。
口径 F17 早写着要核对构建，web 侧有 `x-build-commit`，**这个服务一个都没有**。

两条修法各有分工，都要：
  · **构建标识**：答得出「我是谁」，验收才有资格解释结果；
  · **`extra: forbid`**：同一形态的下一次是 **422 并点名字段**，不是静默错答。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.build import BUILD_COMMIT_ENV_VARS, build_commit
from api.main import create_app
from api.settings import Settings

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import FakeRest, row

ST = Settings(supabase_url="https://example.supabase.co",
              supabase_publishable_key="sb_publishable_test",
              anthropic_api_key=None)


def client(jwks):
    from api.auth import TokenVerifier
    from api.supabase import SupabaseClient

    app = create_app(
        settings=ST,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(ST.rest_url, ST.supabase_publishable_key,
                                transport=FakeRest({USER_A: [row(USER_A)]})),
    )
    return TestClient(app)


class TestBuildCommit:
    def test_none_when_nothing_is_set(self):
        """**取不到是 `None`，不是 `'unknown'`。**

        两者在字符串上都是「有个值」，但含义相反：`None` 是「这个服务答不出
        自己是谁」，而 `'unknown'` 会被读成「它答了，内容是 unknown」。
        验收脚本对前者的处置是**拒绝解释结果**。
        """
        assert build_commit({}) is None
        assert build_commit({"RENDER_GIT_COMMIT": "", "BUILD_COMMIT": "   "}) is None

    @pytest.mark.parametrize("var", BUILD_COMMIT_ENV_VARS)
    def test_each_env_var_is_read(self, var):
        """**两个变量都要被某条断言碰到**（通则七 #7）。

        只测 `BUILD_COMMIT` 的话，Render 注入的那个名字从没被执行过——
        而线上用的恰好是它。
        """
        assert build_commit({var: "abc1234"}) == "abc1234"

    def test_render_wins_over_generic(self):
        """平台注入的优先。本机遗留一个 `BUILD_COMMIT` 不该盖掉线上的真值。"""
        assert build_commit({"RENDER_GIT_COMMIT": "real", "BUILD_COMMIT": "stale"}) == "real"

    def test_health_reports_it(self, jwks, monkeypatch):
        monkeypatch.setenv("BUILD_COMMIT", "deadbee")
        r = client(jwks).get("/health")
        assert r.status_code == 200
        assert r.json()["build_commit"] == "deadbee"

    def test_health_reports_null_when_absent(self, jwks, monkeypatch):
        for v in BUILD_COMMIT_ENV_VARS:
            monkeypatch.delenv(v, raising=False)
        assert client(jwks).get("/health").json()["build_commit"] is None

    def test_header_is_on_every_response_including_errors(self, jwks, monkeypatch):
        """**错误响应上也要有。**

        401 的时候最需要知道「我在跟哪个构建说话」——一个旧构建的鉴权行为
        可能与新构建不同，而那时 `/health` 的 200 说明不了正在被打的那个路由。
        """
        monkeypatch.setenv("BUILD_COMMIT", "deadbee")
        c = client(jwks)
        assert c.get("/health").headers["x-build-commit"] == "deadbee"
        unauth = c.get("/me/style")
        assert unauth.status_code == 401
        assert unauth.headers["x-build-commit"] == "deadbee"

    def test_header_absent_rather_than_unknown(self, jwks, monkeypatch):
        """允许控制：取不到时**这个头不出现**，不是出现一个 'unknown'。"""
        for v in BUILD_COMMIT_ENV_VARS:
            monkeypatch.delenv(v, raising=False)
        assert "x-build-commit" not in client(jwks).get("/health").headers


class TestUnknownFieldsAreRefused:
    """**未知请求字段一律拒。** pydantic 的默认 `extra="ignore"` 真的咬了一次。"""

    def test_unknown_field_is_422_and_names_it(self, jwks, keypair):
        r = client(jwks).post(
            "/explain",
            headers={"Authorization": f"Bearer {keypair.sign()}"},
            json={"question": "Q", "corse_id": "拼错的字段名"},
        )
        assert r.status_code == 422, r.text
        assert "corse_id" in r.text, "422 了但没点名是哪个字段"

    def test_known_fields_still_pass(self, jwks, keypair):
        """允许控制：`course_id` / `session_id` / `turn_index` **必须被接受**。

        少了它，一个「什么字段都拒」的实现也能让上一条全绿，
        而那会让阶段 5–6 的整条路径 422。
        （这里到不了模型，因为没配 ANTHROPIC_API_KEY → 503，
        而 503 恰好证明请求体过了校验。）
        """
        r = client(jwks).post(
            "/explain",
            headers={"Authorization": f"Bearer {keypair.sign()}"},
            json={"question": "Q", "course_id": "cccccccc-3333-4333-8333-cccccccccccc",
                  "session_id": "99999999-9999-4999-8999-999999999999", "turn_index": 2},
        )
        assert r.status_code == 503, r.text
        assert "ANTHROPIC_API_KEY" in r.text

    def test_course_request_also_forbids_extras(self, jwks, keypair):
        r = client(jwks).post(
            "/courses",
            headers={"Authorization": f"Bearer {keypair.sign()}"},
            json={"name": "22.081J", "owner": "想自己指定 owner"},
        )
        assert r.status_code == 422, r.text
