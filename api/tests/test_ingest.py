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
        self.lexicon: list[dict] = []

    def insert_document(self, *, token, row):
        self.document = row
        return {"id": "doc-1", **row}

    def insert_slices(self, *, token, rows):
        self.slices = rows

    def insert_embeddings(self, *, token, rows):
        self.embeddings = rows

    def insert_lexicon(self, *, token, rows):
        self.lexicon = rows


@pytest.fixture
def run():
    def _run(**over):
        # `embedder` 允许覆盖：TestProductionReturnType 要喂一个返回 ndarray 的
        # （生产的返回类型），而默认那个返回 list。`pop` 而不是直接传，
        # 否则 `embedder=emb, **over` 会 TypeError: got multiple values。
        store = StoreSpy()
        emb = over.pop("embedder", None) or FakeEmbedder()
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


class TestProductionReturnType:
    """**假嵌入器必须照生产实现的返回类型写，不是照协议声明写。**

    ## 这一组是被一个真 bug 逼出来的（2026-09-12，第一次真跑摄入）

    `api.ingest.Embedder` 协议原本声明 `-> list[list[float]]`，本文件里所有假嵌入器
    也都返回 `list`。而生产的 `retrieval.embed.OpenAIEmbedder.embed` 返回的是
    **`np.ndarray`**，下游 `if vectors:` 对多元素 ndarray 抛
    `ValueError: The truth value of an array with more than one element is ambiguous`。

    **46 条 pytest 全绿，链路第一次碰到真嵌入器就 422。**

    形态与 F16 的三次复发相同：协议声明、测试替身、下游代码**三端同源**——
    都来自「我写下的那个类型」，没有一端来自生产实现真的返回什么。
    写判据时要问的是「这两端会不会一起变」：会一起变，它就永远相等。
    """

    def test_production_embedder_really_returns_ndarray(self):
        """**锚点：生产实现的返回类型。**

        下面那个 ndarray 假嵌入器照着这个事实写。生产哪天改成返回 list，
        这一条会红 —— 那时要去改假嵌入器，而不是让它静静地停在旧类型上
        （同 F25：结论要锚定它所依据的那一版）。

        断的是**真的调用一次**得到的对象类型，不是类型标注——
        标注是写下来的，可以与实现漂开。
        """
        import numpy as np

        from retrieval.embed import FakeEmbedder as RetrievalFake
        from retrieval.embed import OpenAIEmbedder

        # OpenAIEmbedder 要网络与钱，不在单测里调。取 `retrieval` 自己那个
        # 假嵌入器作为该模块返回类型的代表：它与 OpenAIEmbedder 同在 embed.py，
        # 同走 `_l2()` 归一化那条出口 —— 返回类型由那一行决定，不由哪个实现决定。
        out = RetrievalFake(dimensions=1024).embed(["一段文本", "另一段"])
        assert isinstance(out, np.ndarray), (
            f"retrieval 侧嵌入器的返回类型变成了 {type(out).__name__}。"
            "本文件的 NumpyEmbedder 是照 ndarray 写的，要一起改。"
        )
        # `_l2` 是两个实现共用的出口，OpenAIEmbedder 的最后一行就是 `return _l2(...)`。
        import inspect

        src = inspect.getsource(OpenAIEmbedder.embed)
        assert "_l2(" in src, (
            "OpenAIEmbedder.embed 不再走 _l2() 出口了，"
            "上面那条『用 retrieval 的假嵌入器代表返回类型』的推理随之失效。"
        )

    def test_numpy_returning_embedder_goes_through(self, run):
        """**生产形态的假嵌入器：返回 ndarray。** 这一条就是那个 bug 的复现。

        修之前它抛 `ValueError: The truth value of an array ... is ambiguous`；
        修之后整条链走通，且落库的 `embedding` 是 pgvector 的文本形式。
        """
        import numpy as np

        class NumpyEmbedder(FakeEmbedder):
            """与 `retrieval.embed.OpenAIEmbedder` **同一个返回类型**。"""

            def embed(self, texts):
                return np.asarray(super().embed(texts), dtype=np.float64)

        result, store, _ = run(embedder=NumpyEmbedder())
        assert result.embedded_count == result.slice_count > 0
        assert len(store.embeddings) == result.slice_count
        # 序列化不得因为来源是 np.float64 就变形（'np.float64(0.0)' 那种）
        first = store.embeddings[0]["embedding"]
        assert first.startswith("[") and first.endswith("]"), first[:60]
        assert len(first.strip("[]").split(",")) == 1024
        assert "np.float64" not in first, "np.float64 的 repr 漏进了 pgvector 的文本形式"

    def test_list_returning_embedder_still_works(self, run):
        """允许控制：返回 list 的嵌入器仍然要能跑。

        少了它，一个「只接受 ndarray」的实现也会让上一条全绿，
        而本仓库其余全部假嵌入器返回的都是 list。
        """
        result, store, _ = run()
        assert result.embedded_count == result.slice_count > 0
        assert len(store.embeddings[0]["embedding"].strip("[]").split(",")) == 1024


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
    """23505 必须翻译成人话 —— 数据库那条是硬失败不是提示。

    ## ⚠️ 这一组曾经全绿，而生产路径是坏的（2026-09-12 第一次真跑摄入抓出来）

    旧断言喂的假错误正文是**我手写的**，里面有一段
    `"details":"Key (owner, course_id, content_sha256)=(...) already exists."`，
    于是 `api/api/supabase.py` 里那条 `"23505" in body and "content_sha256" in body`
    顺利命中。

    **真库返回的正文里 `details` 是 `null`**，整份正文里没有 `content_sha256`
    这个词。真实重传得到的是 `502 数据库返回 409：{"code":"23505",...}`——
    正好是那条注释说要避免的「把数据库错误码丢给用户」。

    形态与 F16 的三次复发相同：**判据的两端同源**——假正文与匹配条件
    出自同一个人的同一个猜测，它们必然相等。

    两处改动：
      ① 匹配条件不再认正文里的任何字符串，只认**错误码 + 调用点**（见 supabase.py）；
      ② 下面这份假正文是**真库上实际观测到的那一份，逐字抄录**，不是手写的。
    """

    #: 🔒 **真库观测原文**（2026-09-12，`POST /courses/{A}/documents` 重传同一份文件）。
    #: 抄录，不重写。改它之前先去真库重现一次——手写一份「看起来像」的正文，
    #: 就是这一组曾经全绿而生产是坏的那个原因。
    REAL_23505_BODY = (
        '{"code":"23505","details":null,"hint":null,'
        '"message":"duplicate key value violates unique constraint '
        '\\"documents_owner_course_sha_idx\\""}'
    )

    def _client_with_body(self, status: int, body: str):
        import httpx

        from api.supabase import SupabaseClient

        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = status
                text = body
                content = b"x"
            return R()

        client = SupabaseClient("https://x/rest/v1", "sb_publishable_x")
        return client, fake_post

    def test_real_prod_23505_body_becomes_a_409_with_chinese(self):
        """**用真库那份正文**跑一遍。旧实现在这一条上是红的。"""
        import httpx

        from api.supabase import DuplicateDocument

        client, fake_post = self._client_with_body(409, self.REAL_23505_BODY)
        orig, httpx.post = httpx.post, fake_post
        try:
            with pytest.raises(DuplicateDocument) as e:
                client.insert_document(token="t", row={"content_sha256": "x"})
            assert e.value.status == 409
            assert "同一份文件" in e.value.message
            assert "23505" not in e.value.message, "数据库错误码漏给用户了"
        finally:
            httpx.post = orig

    def test_real_body_does_not_contain_the_column_name(self):
        """**钉住那个 bug 本身**：真正文里没有 `content_sha256`。

        少了这一条，有人会把匹配条件改回「认列名」而上一条照样绿——
        因为上一条只说「现在能翻译」，没说「靠什么翻译」。
        """
        assert "content_sha256" not in self.REAL_23505_BODY
        assert "documents_owner_course_sha_idx" in self.REAL_23505_BODY

    def test_other_errors_are_not_swallowed_as_duplicate(self):
        """反向控制：**不是 23505 的失败不许被当成重复。**

        少了它，一个「documents 上任何 4xx 都报重复」的实现会让上面两条全绿，
        而 RLS 拦住（403）会被说成「已经有同一份文件了」——用户按这句话去删文件，
        删完还是进不去。
        """
        import httpx

        from api.supabase import DuplicateDocument, SupabaseError

        client, fake_post = self._client_with_body(
            403, '{"code":"42501","message":"new row violates row-level security policy"}')
        orig, httpx.post = httpx.post, fake_post
        try:
            with pytest.raises(SupabaseError) as e:
                client.insert_document(token="t", row={})
            assert not isinstance(e.value, DuplicateDocument)
            assert "42501" in e.value.message
        finally:
            httpx.post = orig

    def test_non_document_inserts_never_translate(self):
        """反向控制：`insert_slices` 上的 23505 **不**翻译成「重复文件」。

        `duplicate_message` 只在 `insert_document` 那个调用点给。
        切片主键冲突是另一件事，报成「这门课里已经有同一份文件了」
        会把人指向一个完全不相干的操作。
        """
        import httpx

        from api.supabase import DuplicateDocument, SupabaseError

        client, fake_post = self._client_with_body(409, self.REAL_23505_BODY)
        orig, httpx.post = httpx.post, fake_post
        try:
            with pytest.raises(SupabaseError) as e:
                client.insert_slices(token="t", rows=[{"slice_id": "s001"}])
            assert not isinstance(e.value, DuplicateDocument)
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
        from parse_service.limits import MAX_UPLOAD_BYTES

        assert _max_upload_bytes() == MAX_UPLOAD_BYTES
