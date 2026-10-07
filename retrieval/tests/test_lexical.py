"""词法检索的分词与 RRF（口径 F97）。每条判据配一条反向控制。"""

from __future__ import annotations

from retrieval.lexical import RRF_K, lexical_text, rrf_fuse, tokens, tsquery


class TestTokens:
    def test_plural_and_tense_land_on_the_same_term(self):
        assert tokens("engines")[0] == tokens("engine")[0]
        assert tokens("efficiencies")[0] == tokens("efficiency")[0]
        assert tokens("rejected")[0] == tokens("reject")[0]

    def test_reverse_control_without_stemming_they_differ(self, monkeypatch):
        """**反向验证**：把词尾归一换成恒等，上一条必红——证明它不是两边恰好相同。"""
        import retrieval.lexical as L

        monkeypatch.setattr(L, "_stem", lambda w: w)
        assert tokens("engines")[0] != tokens("engine")[0]

    def test_cjk_is_bigrammed(self):
        assert tokens("热机效率") == ["热机", "机效", "效率"]
        assert tokens("热") == ["热"]

    def test_mixed_text_keeps_both_scripts(self):
        t = tokens("卡诺效率 Carnot efficiency U-235")
        assert "卡诺" in t and "carnot" in t and "efficiency" in t and "235" in t

    def test_stopwords_dropped(self):
        assert tokens("the efficiency of the engine") == ["efficiency", "engine"]

    def test_index_and_query_share_one_function(self):
        """索引侧与查询侧走同一个 `tokens`：同一个词在两侧落成同一个词条。"""
        idx = lexical_text("REAL HEAT ENGINES", "Carnot efficiency")
        q = tsquery("heat engine")
        for term in ("heat", "engine"):
            assert term in idx.split()
            assert f"'{term}'" in q


class TestTsquery:
    def test_or_joined_and_quoted(self):
        assert tsquery("heat engine") == "'heat' | 'engine'"

    def test_dedup(self):
        assert tsquery("heat heat", "heat") == "'heat'"

    def test_empty_means_skip(self):
        assert tsquery("", None, "the of") == ""

    def test_no_injection_possible(self):
        q = tsquery("a' | !b & (c")
        assert "!" not in q and "&" not in q and "(" not in q
        for part in q.split(" | "):
            assert part.startswith("'") and part.endswith("'") and "'" not in part[1:-1]


class TestRrf:
    def test_both_channels_beat_one(self):
        a, b, c = ("d", "s1"), ("d", "s2"), ("d", "s3")
        fused = [k for k, _ in rrf_fuse([a, b], [c, b])]
        assert fused[0] == b, "两路都排第二的应当胜过只在一路排第一的"

    def test_reverse_control_single_channel_would_not(self):
        """**反向验证**：只看向量那一路，b 不会第一——证明上一条是融合带来的。"""
        a, b = ("d", "s1"), ("d", "s2")
        assert [k for k, _ in rrf_fuse([a, b])][0] == a

    def test_scores_follow_the_formula(self):
        a = ("d", "s1")
        (_, s), = rrf_fuse([a], [a])
        assert abs(s - 2 / (RRF_K + 1)) < 1e-12

    def test_deterministic_ties(self):
        a, b = ("d", "s1"), ("d", "s2")
        assert rrf_fuse([a], [b]) == rrf_fuse([a], [b])
        assert [k for k, _ in rrf_fuse([a], [b])] == [a, b]


class TestComposeT0IsTheSameFunction:
    def test_t0_matches_its_documented_definition(self):
        """重建索引时用 `compose_t0`（手里只有落库的 units），摄入时用 `compose(…, "T0")`。

        ⚠️ **不拿两者互比**：`compose` 的 T0 分支调的就是 `compose_t0`，互比恒真（F16）。
        期望值按 compose 文档里的 T0 定义**在这里独立算**：块按顺序、逐块清理、
        跳过空块、换行拼接；全空时退回清理后的原文。
        """
        import re
        import unicodedata

        from retrieval.compose import compose
        from retrieval.corpus import load_slicesets

        def clean(t):
            out = []
            for ch in t:
                if ch in "\n\t":
                    out.append(ch)
                elif unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Cn"):
                    out.append(" ")
                else:
                    out.append(ch)
            return re.sub(r"[ \t]+", " ", "".join(out))

        n = 0
        for ss in load_slicesets().values():
            got = compose(ss, "T0")
            for sl in ss.slices:
                body = "\n".join(c for c in (clean(u.text) for u in sl.units) if c.strip()).strip()
                want = body or clean(sl.text).strip() or sl.slice_id
                assert got[sl.slice_id] == want, sl.slice_id
                n += 1
        assert n > 50, f"只比了 {n} 片——语料没装上？"
