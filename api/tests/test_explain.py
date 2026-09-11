"""阶段 3：`style_config` 那一行**第一次成为一次讲解生成的输入**。

## 本文件测到哪里为止

端到端那条链有四段：**库里那一行 → StyleConfig → prompt → 模型输出**。
前三段在这里离线钉死；**第四段（模型服从不服从 prompt）需要真调模型**，
在 `scripts/acceptance_p0_p3.py`，属人工验收。

但第四段不是唯一的下游：配置**同时驱动判定**（口径 X05）。
`TestConfigReachesTheJudgement` 用同一段固定文本在 P0 与 P3 下各判一次，
结论必须相反——那证明库里那一行真的进了 `RuleReviewer`，
**不需要模型参与**，也就不受「模型这次恰好听话」的影响。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.auth import TokenVerifier
from api.main import create_app
from api.settings import Settings
from api.supabase import SupabaseClient

from conftest import AUDIENCE, ISSUER, USER_A, USER_B, StaticJwks
from fake_db import FakeRest, row

SETTINGS = Settings(
    supabase_url="https://example.supabase.co",
    supabase_publishable_key="sb_publishable_test",
    anthropic_api_key="sk-ant-fake-for-tests",
)


def build(jwks, rest, *, model=None, settings=SETTINGS) -> TestClient:
    from explain.fake import FakeExplainModel

    app = create_app(
        settings=settings,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(settings.rest_url, settings.supabase_publishable_key, transport=rest),
        model_factory=(lambda: model) if model is not None else (lambda: FakeExplainModel()),
    )
    return TestClient(app, raise_server_exceptions=False)


def ask(client, keypair, sub=USER_A, q="Bloom filter 的误判率是怎么来的？"):
    return client.post(
        "/explain",
        json={"question": q},
        headers={"Authorization": f"Bearer {keypair.sign(sub=sub)}"},
    )


@pytest.fixture
def rest():
    """两个账号：一个 P0，一个 P3。其余三条轴也不同，供 B 类断言用。"""
    return FakeRest({
        USER_A: [row(USER_A, detail_level="P0")],
        USER_B: [row(USER_B, detail_level="P3", use_examples="fewer",
                     structure="buildup_first", depth="derivation")],
    })


class TestConfigReachesThePrompt:
    """库里那一行 → prompt。**四条轴都要能在 prompt 里被看见。**"""

    def test_detail_level_changes_the_prompt(self, keypair, jwks, rest):
        c = build(jwks, rest)
        a = ask(c, keypair, USER_A).json()
        b = ask(c, keypair, USER_B).json()
        assert a["style_rules"] != b["style_rules"], "P0 与 P3 的 prompt 一模一样 —— 档位没进 prompt"
        assert "30–120 字" in a["style_rules"], a["style_rules"]
        assert "15–40 字" in b["style_rules"], b["style_rules"]

    def test_b_class_axes_reach_the_prompt(self, keypair, jwks, rest):
        """**口径 F45**：B 类不判定 ≠ B 类不进 prompt。

        不渲染的话，F1 那 6 步成对比较里有 4 步在采集一个对输出毫无影响的东西。
        """
        c = build(jwks, rest)
        a = ask(c, keypair, USER_A).json()["style_rules"]
        b = ask(c, keypair, USER_B).json()["style_rules"]
        assert "多举例子" in a and "少举例子" in b
        assert "先给结论" in a and "先铺垫后收结论" in b
        assert "讲到为什么" in a and "讲到推导" in b

    def test_prompt_has_both_classes_in_the_sample_shape(self, keypair, jwks, rest):
        """A 类三条 + B 类三条，拼法与 `pairs.json` 里 16 个样例同形（口径 F19）。"""
        from explain.style_axes import style_config_for
        from explain.style_prompt import render_all_style_rules

        rules = ask(build(jwks, rest), keypair, USER_A).json()["style_rules"]
        expected = render_all_style_rules(
            style_config_for("P0"),
            {"use_examples": "more", "structure": "conclusion_first", "depth": "why"},
        )
        assert expected in rules
        assert len([ln for ln in expected.splitlines() if ln.startswith("- ")]) == 6

    def test_fingerprint_differs_between_tiers(self, keypair, jwks, rest):
        """验收要靠它把「配置没生效」与「两次配置本来就一样」分开。"""
        c = build(jwks, rest)
        assert ask(c, keypair, USER_A).json()["config_fingerprint"] != \
               ask(c, keypair, USER_B).json()["config_fingerprint"]


class TestConfigReachesTheJudgement:
    """库里那一行 → `RuleReviewer`。**同一段文本，两个档位下结论相反。**

    这一条不需要模型参与，所以它不受「模型这次恰好听话」影响——
    它单独证明了配置真的驱动判定（口径 X05：配置同时驱动 prompt 与判定）。
    """

    def test_same_text_passes_p0_and_fails_p3(self, keypair, jwks, rest):
        from explain.fake import FakeExplainModel

        # 每段约 70 字：落在 P0 的 30–120 里，落在 P3 的 15–40 之外。
        # `corrected_chars` 也设成 70 —— 不让假模型「收到反馈就改好」，
        # 否则 P3 那条会因为重生成成功而变绿，测到的就不是判定了。
        stubborn = FakeExplainModel(target_chars=70, corrected_chars=70)
        c = build(jwks, rest, model=stubborn)

        a = ask(c, keypair, USER_A).json()
        b = ask(c, keypair, USER_B).json()

        assert a["all_activated_passed"] is True, f"P0 下 70 字/段应当达标：{a}"
        assert b["all_activated_passed"] is False, f"P3 下 70 字/段应当不达标：{b}"
        assert a["accepted"] is True and b["accepted"] is False
        # P3 不达标 → 触发重生成直到触顶（PRD §10 降级输出）
        assert b["regenerations"] > 0, "P3 下不达标却没有触发重生成"
        assert a["regenerations"] == 0

    def test_target_range_comes_from_the_row(self, keypair, jwks, rest):
        c = build(jwks, rest)
        assert ask(c, keypair, USER_A).json()["paragraph_length_target"] == {"min": 30, "max": 120}
        assert ask(c, keypair, USER_B).json()["paragraph_length_target"] == {"min": 15, "max": 40}


class TestRefusesToGuess:
    def test_unknown_tier_is_500_not_defaulted(self, keypair, jwks):
        """库里出现取值域外的档位 → **判失败，不回落默认值**。

        回落会让「用户选了 P3」与「库里有个我们不认识的值」在输出里长得一模一样。
        """
        c = build(StaticJwks(keypair), FakeRest({USER_A: [row(USER_A, detail_level="P1")]}))
        r = ask(c, keypair)
        assert r.status_code == 500 and "P1" in r.json()["detail"]

    def test_missing_model_key_is_503_and_says_so(self, keypair, jwks, rest):
        """缺 key 是部署配置问题，不是用户输入问题。"""
        bare = Settings(supabase_url=SETTINGS.supabase_url,
                        supabase_publishable_key=SETTINGS.supabase_publishable_key,
                        anthropic_api_key=None)
        app = create_app(
            settings=bare,
            verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
            supabase=SupabaseClient(bare.rest_url, bare.supabase_publishable_key, transport=rest),
        )
        r = TestClient(app, raise_server_exceptions=False).post(
            "/explain", json={"question": "x"},
            headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]

    def test_explain_needs_auth(self, jwks, rest):
        assert build(jwks, rest).post("/explain", json={"question": "x"}).status_code == 401
