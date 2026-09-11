"""阶段 4 · 摄入：上传 → 解析 → 切片 → 组文本 → 嵌入 → 落库。

## 真解析、真切片、假嵌入

省掉的**只有**那次网络调用与那笔钱：`parse_pdf` 与 `slice_document` 走的是
生产的同一份代码，喂的是仓库里的真 PDF。嵌入是唯一要出境的一步
（PRD §13.3 的第二次出境），所以它注入。

用假解析器的话，「切片数对不对」「受限页排没排掉」这些断言测的就是我自己写的
那个假货（F28 说的「用一个乐观的替身冒充那个环节」）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.ingest import ingest_document

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import FakeRest

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "实验脚本" / "测试集"
# 最小的一份，跑得快。它没有受限页（受限页在 14.129 上）。
DECK = CORPUS / "MIT22_081JF10_lec01b.pdf"
COURSE = "cccccccc-3333-4333-8333-cccccccccccc"


class FakeEmbedder:
    """确定性假嵌入器：按文本长度铺一个可预测的向量。

    **它不假装自己是语义模型**——任何「召回准不准」的断言都不得建立在它之上。
    这里只用它回答两件事：维度对不对、条数对不对。
    """

    name = "fake-embedder"
    dimensions = 1024

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            v = [0.0] * self.dimensions
            v[len(t) % self.dimensions] = 1.0
            out.append(v)
        return out


class StoreSpy:
    """记下每一次写入，供断言检查落库内容。"""

    def __init__(self) -> None:
        self.document: dict | None = None
        self.slices: list[dict] = []
        self.embeddings: list[dict] = []

    def insert_document(self, *, token, row):
        self.document = row
        return {"id": "doc-1", **row}

    def insert_slices(self, *, token, rows):
        self.slices = rows

    def insert_embeddings(self, *, token, rows):
        self.embeddings = rows


@pytest.fixture
def run():
    def _run(**over):
        store, emb = StoreSpy(), FakeEmbedder()
        result = ingest_document(
            data=DECK.read_bytes(), filename=DECK.name, owner=USER_A,
            course_id=COURSE, token="tok", store=store, embedder=emb, **over,
        )
        return result, store, emb
    return _run


class TestTheChainRuns:
    def test_produces_slices_and_one_vector_each(self, run):
        result, store, emb = run()
        assert result.slice_count > 0, "真 PDF 切出 0 个切片 —— 链路没跑通"
        assert len(store.slices) == result.slice_count
        assert len(store.embeddings) == result.slice_count, "切片与向量条数必须一一对应"
        assert len(emb.calls) == 1, "嵌入应当整份一次算完"

    def test_embeds_the_composed_text_not_the_raw_slice(self, run):
        """喂给嵌入的是 `compose()` 的产物，不是切片原文。

        两者不同（compose 会加文档前缀、去页眉页脚），而 `embed_ruleset` 记的
        正是 compose 的口径版本——喂错了那个版本号就在说谎。
        """
        _, store, emb = run()
        composed = emb.calls[0]
        raw = [s["text"] for s in store.slices]
        assert len(composed) == len(raw)
        assert composed != raw, "喂进嵌入的与切片原文逐条相同 —— compose 没被调用"

    def test_vector_is_pgvector_text_format(self, run):
        _, store, _ = run()
        v = store.embeddings[0]["embedding"]
        assert isinstance(v, str) and v.startswith("[") and v.endswith("]")
        assert len(v[1:-1].split(",")) == 1024, "维度必须是 1024（= slice_embeddings 的列定义）"


class TestVersionsAreRecorded:
    """五个口径版本列。漏一个，新旧行事后再也分不开。"""

    def test_all_five_present(self, run):
        _, store, _ = run()
        doc = store.document
        for col in ("parse_schema_version", "slice_schema_version",
                    "slicer_version", "slice_ruleset_version"):
            assert doc[col], f"{col} 没写进去"
        assert "split_threshold_chars" in doc, "split_threshold_chars 这个键必须在"

    def test_split_threshold_none_is_recorded_as_null_not_zero(self, run):
        """NULL 有含义（= 不拆），**不能填 0**。

        填 0 的话「没拆」与「阈值是 0」变成同一个值，而后者是非法输入
        （`slice_document` 会拒）。这一列今天恒为 None，正因如此最容易被填错。
        """
        _, store, _ = run()
        assert store.document["split_threshold_chars"] is None

    def test_injected_threshold_is_recorded(self, run):
        """反向控制：真传了一个值，它必须落库。

        少了它，一个「永远写 None」的实现也能让上一条全绿。
        """
        _, store, _ = run(split_threshold_chars=1500)
        assert store.document["split_threshold_chars"] == 1500

    def test_versions_come_from_the_real_modules(self, run):
        from slicer.schema import SLICE_RULESET_VERSION, SLICE_SCHEMA_VERSION, SLICER_VERSION

        _, store, _ = run()
        assert store.document["slice_schema_version"] == SLICE_SCHEMA_VERSION
        assert store.document["slicer_version"] == SLICER_VERSION
        assert store.document["slice_ruleset_version"] == SLICE_RULESET_VERSION


class TestRestrictedPages:
    """受限页**不入库**，不是「入库但不检索」。"""

    def test_restricted_pages_are_excluded(self, run):
        base, _, _ = run()
        # 挑真实存在的页号：拿第一个切片覆盖的页去排除它。
        _, store, _ = run()
        victim = store.slices[0]["page_indices"][0]
        after, store2, _ = run(restricted_pages=frozenset({victim}))
        assert after.restricted_pages_excluded >= 1
        assert after.slice_count < base.slice_count, "排除了页，切片数却没少"
        assert all(victim not in s["page_indices"] for s in store2.slices)
        assert len(store2.embeddings) == after.slice_count, "被排除的页不该有向量"

    def test_nothing_excluded_by_default(self, run):
        """允许控制：不给受限页清单时一个都不排除。

        ⚠️ **真实用户上传的课件不在 `RESTRICTED_PAGES` 那张清单上**
        （它按测试集文件名写死，以 SOURCES.md 为准），所以这一层对他们恒不触发。
        如实登记，不写成「受限页已排除」。
        """
        result, _, _ = run()
        assert result.restricted_pages_excluded == 0


class TestRefusesToGuess:
    def test_mis_declared_embedder_is_rejected(self, run):
        """嵌入器自称 512 维 → 拒。

        ⚠️ 这条抓到过一个真 bug：第一版的检查是
        `len(vectors[0]) != embedder.dimensions` —— **拿嵌入器跟它自己比**，
        一个声称 512 又返回 512 的嵌入器照样通过（口径 F16：永远不会失败的
        检查不是检查）。现在比的是 `SELECTED_DIMENSIONS`，即 SQL 列定义钉着的那个数。
        """
        class WrongDim(FakeEmbedder):
            dimensions = 512

            def embed(self, texts):
                return [[0.0] * 512 for _ in texts]

        store = StoreSpy()
        with pytest.raises(ValueError, match="512"):
            ingest_document(data=DECK.read_bytes(), filename=DECK.name, owner=USER_A,
                            course_id=COURSE, token="t", store=store, embedder=WrongDim())

    def test_right_dimension_passes(self, run):
        """允许控制：1024 维的嵌入器必须通过。

        少了它，一个「任何嵌入器都拒」的实现也能让上一条全绿。
        """
        result, _, _ = run()
        assert result.embedded_count > 0

    def test_lying_embedder_is_rejected(self, run):
        """声称 1024 但返回 512 → 也要拒（前一条查声明，这条查实物）。"""
        class Liar(FakeEmbedder):
            def embed(self, texts):
                return [[0.0] * 512 for _ in texts]

        store = StoreSpy()
        with pytest.raises(ValueError, match="向量维度"):
            ingest_document(data=DECK.read_bytes(), filename=DECK.name, owner=USER_A,
                            course_id=COURSE, token="t", store=store, embedder=Liar())

    def test_count_mismatch_raises(self):
        class ShortEmbedder(FakeEmbedder):
            def embed(self, texts):
                return super().embed(texts)[:-1]

        store = StoreSpy()
        with pytest.raises(ValueError, match="嵌入返回"):
            ingest_document(data=DECK.read_bytes(), filename=DECK.name, owner=USER_A,
                            course_id=COURSE, token="t", store=store, embedder=ShortEmbedder())


class TestDuplicateIsTranslated:
    """23505 必须翻译成人话 —— 数据库那条是硬失败不是提示。"""

    def test_23505_becomes_a_409_with_chinese(self):
        from api.supabase import DuplicateDocument, SupabaseClient

        class Rejecting:
            def __call__(self, url, headers, body=None):
                raise AssertionError("不该走到这里")

        import httpx

        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = 409
                text = '{"code":"23505","message":"duplicate key value violates unique constraint \\"documents_owner_course_sha_idx\\"","details":"Key (owner, course_id, content_sha256)=(...) already exists."}'
                content = b"x"
            return R()

        client = SupabaseClient("https://x/rest/v1", "sb_publishable_x")
        orig = httpx.post
        httpx.post = fake_post
        try:
            with pytest.raises(DuplicateDocument) as e:
                client.insert_document(token="t", row={"content_sha256": "x"})
            assert e.value.status == 409
            assert "同一份文件" in e.value.message
        finally:
            httpx.post = orig


class TestUploadRoute:
    """路由层：鉴权、类型、体积、23505 翻译。"""

    def _client(self, jwks, rest, embedder=None):
        from fastapi.testclient import TestClient
        from api.auth import TokenVerifier
        from api.main import create_app
        from api.settings import Settings
        from api.supabase import SupabaseClient

        st = Settings(supabase_url="https://example.supabase.co",
                      supabase_publishable_key="sb_publishable_test",
                      anthropic_api_key=None)
        app = create_app(
            settings=st,
            verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
            supabase=SupabaseClient(st.rest_url, st.supabase_publishable_key, transport=rest),
            embedder_factory=(lambda: embedder) if embedder else None,
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_upload_needs_auth(self, jwks):
        c = self._client(jwks, FakeRest({}))
        r = c.post(f"/courses/{COURSE}/documents", files={"file": ("a.pdf", b"x", "application/pdf")})
        assert r.status_code == 401

    def test_pptx_is_501_not_415(self, keypair, jwks):
        """「还没做」与「不支持」分开报——压成一句会让人去做别的兜底。"""
        c = self._client(jwks, FakeRest({}))
        r = c.post(f"/courses/{COURSE}/documents",
                   files={"file": ("a.pptx", b"x", "application/vnd.ms-powerpoint")},
                   headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 501 and "PPTX" in r.json()["detail"]

    def test_non_pdf_is_415(self, keypair, jwks):
        c = self._client(jwks, FakeRest({}))
        r = c.post(f"/courses/{COURSE}/documents", files={"file": ("a.txt", b"x", "text/plain")},
                   headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 415

    def test_empty_file_is_400(self, keypair, jwks):
        c = self._client(jwks, FakeRest({}))
        r = c.post(f"/courses/{COURSE}/documents", files={"file": ("a.pdf", b"", "application/pdf")},
                   headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 400

    def test_real_pdf_goes_all_the_way_through(self, keypair, jwks):
        """允许控制：一份真 PDF 必须能走完整条链并落库。

        少了它，上面四条在「上传永远失败」的实现下同样全绿。
        """
        rest = FakeRest({})
        c = self._client(jwks, rest, embedder=FakeEmbedder())
        r = c.post(f"/courses/{COURSE}/documents",
                   files={"file": (DECK.name, DECK.read_bytes(), "application/pdf")},
                   headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["slice_count"] > 0 and body["embedded_count"] == body["slice_count"]
        # 三张表都被写到了，且带的是用户自己的 token（RLS 由数据库执行）
        written = [u for u, _ in rest.writes]
        assert any("/documents" in u for u in written)
        assert any("/slices" in u for u in written)
        assert any("/slice_embeddings" in u for u in written)

    def test_upload_limit_comes_from_parse_service(self):
        """体积上限不在这里写第二个数字。"""
        from api.main import _max_upload_bytes
        from parse_service.app import MAX_UPLOAD_BYTES

        assert _max_upload_bytes() == MAX_UPLOAD_BYTES
