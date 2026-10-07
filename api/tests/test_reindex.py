"""重建索引（`api/api/reindex.py` + `POST /courses/{id}/reindex`，口径 F97 / F99）。

守三件事：**口径切换是最后一步**（之前检索仍用老行）、**先删后写**（重跑不撞主键）、
**补不了的东西要说出来**（老文档的图片页转写）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.indexing import INDEX_RULESET, LEGACY_RULESET
from api.reindex import reindex_document

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import row

DOC = "dddddddd-1111-4111-8111-dddddddddddd"
COURSE = "cccccccc-3333-4333-8333-cccccccccccc"


class Emb:
    name = "fake"
    dimensions = 1024

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, texts):
        self.texts.extend(texts)
        return [[0.0] * 1024 for _ in texts]


class Ctx:
    def contextualize(self, filename, slices):
        from explain.rag import ContextResult

        return ContextResult({sid: f"定位 {sid}" for sid, *_ in slices}, 0.02, 1, 0)


class OpLog:
    """按顺序记下每一个写操作——「切换是最后一步」要靠顺序来断。"""

    def __init__(self, slices: list[dict[str, Any]]) -> None:
        self._slices = slices
        self.ops: list[tuple[str, Any]] = []

    def slices_for_reindex(self, token, document_id):
        return self._slices

    def delete_index_rows(self, *, token, document_id, ruleset):
        self.ops.append(("delete", ruleset))

    def insert_embeddings(self, *, token, rows):
        self.ops.append(("embeddings", rows))

    def insert_lexicon(self, *, token, rows):
        self.ops.append(("lexicon", rows))

    def update_document(self, *, token, document_id, fields):
        self.ops.append(("switch", fields))


SLICES = [
    {"document_id": DOC, "slice_id": "s001", "page_indices": [1], "title": "REAL HEAT ENGINES",
     "text": "A heat engine ...", "units": [{"text": "A heat engine"}, {"text": " converts heat"}]},
    {"document_id": DOC, "slice_id": "v002", "page_indices": [2], "title": "第 2 页（图像转写，可能有误）",
     "text": "图里写着 Carnot", "units": []},
]


def run(**kw):
    store = OpLog(kw.pop("slices", SLICES))
    emb = Emb()
    out = reindex_document(store=store, token="t", owner=USER_A,
                           document=kw.pop("document", {"id": DOC, "filename": "f.pdf", "unusable_pages": []}),
                           embedder=emb, contextualizer=kw.pop("ctx", Ctx()))
    return out, store, emb


class TestOrder:
    def test_switch_is_the_last_step(self):
        _, store, _ = run()
        kinds = [k for k, _ in store.ops]
        assert kinds == ["delete", "embeddings", "lexicon", "switch"]
        assert store.ops[-1][1] == {"index_ruleset": INDEX_RULESET}

    def test_delete_only_touches_the_new_ruleset(self):
        _, store, _ = run()
        assert store.ops[0] == ("delete", INDEX_RULESET)
        assert INDEX_RULESET != LEGACY_RULESET

    def test_embedder_failure_leaves_the_old_index_untouched(self):
        """嵌入炸了 → 一行都没删、口径没切：检索照旧按老口径工作。"""

        class Boom(Emb):
            def embed(self, texts):
                raise RuntimeError("openai down")

        store = OpLog(SLICES)
        with pytest.raises(RuntimeError):
            reindex_document(store=store, token="t", owner=USER_A,
                             document={"id": DOC, "filename": "f.pdf"}, embedder=Boom(), contextualizer=None)
        assert store.ops == []


class TestContent:
    def test_t0_is_rebuilt_from_units_and_context_prefixed(self):
        _, _, emb = run()
        assert emb.texts[0] == "定位 s001\n\nA heat engine\n converts heat"
        assert emb.texts[1] == "定位 v002\n\n图里写着 Carnot", "转写片没有 units，组文本是原文"

    def test_rows_carry_the_new_ruleset(self):
        _, store, _ = run()
        emb_rows = dict(store.ops)["embeddings"]
        lex_rows = dict(store.ops)["lexicon"]
        assert {r["embed_ruleset"] for r in emb_rows} == {INDEX_RULESET}
        assert {r["ruleset"] for r in lex_rows} == {INDEX_RULESET}
        assert lex_rows[0]["context"] == "定位 s001"
        assert "定位" in lex_rows[0]["lexical_text"].split()

    def test_vision_gap_is_reported_not_hidden(self):
        out, _, _ = run(document={"id": DOC, "filename": "f.pdf",
                                  "unusable_pages": [{"index": 3, "kind": "image_only"}]})
        assert out["vision_not_backfilled"] is True

    def test_no_contextualizer_still_reindexes_lexicon(self):
        out, store, _ = run(ctx=None)
        assert out["contextualized"] == 0
        assert dict(store.ops)["lexicon"]


class TestRoute:
    def test_only_legacy_documents_are_touched_and_failures_are_listed(self, jwks, keypair):
        from api.auth import TokenVerifier
        from api.main import create_app
        from api.settings import Settings
        from api.supabase import SupabaseClient

        docs = [
            {"id": DOC, "filename": "old.pdf", "index_ruleset": LEGACY_RULESET, "unusable_pages": []},
            {"id": "dddddddd-2222-4222-8222-dddddddddddd", "filename": "new.pdf",
             "index_ruleset": INDEX_RULESET, "unusable_pages": []},
        ]
        methods: list[tuple[str, str]] = []

        def transport(url, headers, body=None):
            method = headers.get("x-method", "POST" if body is not None else "GET")
            methods.append((method, url.split("/rest/v1")[-1].split("?")[0]))
            if method == "GET":
                if "/style_config" in url:
                    return [row(USER_A)]
                if "/courses" in url:
                    return [{"id": COURSE, "name": "课"}]
                if "/documents" in url:
                    return docs
                if "/slices" in url:
                    return SLICES
            return None

        st = Settings(supabase_url="https://example.supabase.co",
                      supabase_publishable_key="sb_publishable_test", anthropic_api_key=None)
        app = create_app(settings=st, verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
                         supabase=SupabaseClient(st.rest_url, st.supabase_publishable_key, transport=transport),
                         embedder_factory=lambda: Emb())
        r = TestClient(app).post(f"/courses/{COURSE}/reindex",
                                 headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["already_current"] == 1
        assert [d["document_id"] for d in body["reindexed"]] == [DOC]
        assert body["failed"] == []
        assert ("PATCH", "/documents") in methods
        assert ("DELETE", "/slice_embeddings") in methods and ("DELETE", "/slice_lexicon") in methods


def test_retrieve_rejects_unknown_ablation(jwks, keypair):
    """拼错一个环节名 = 什么都没关，两组对照会是同一个东西 → **422，不静默收下**。"""
    from api.auth import TokenVerifier
    from api.main import create_app
    from api.settings import Settings
    from api.supabase import SupabaseClient

    st = Settings(supabase_url="https://example.supabase.co",
                  supabase_publishable_key="sb_publishable_test", anthropic_api_key=None)
    app = create_app(settings=st, verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
                     supabase=SupabaseClient(st.rest_url, st.supabase_publishable_key,
                                             transport=lambda *a, **k: []),
                     embedder_factory=lambda: Emb())
    r = TestClient(app).post(f"/courses/{COURSE}/retrieve",
                             json={"question": "q", "ablate": ["rerankk"]},
                             headers={"Authorization": f"Bearer {keypair.sign()}"})
    assert r.status_code == 422
