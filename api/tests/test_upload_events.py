"""阶段 6 第一批 · 上传页的两个事件 + 失败分类闭集 + 先查后提示。

## 三组，各防一件事

1. **镜像比对**：`FAILURE_KINDS` 与 `REQUIRED_KEYS` 的新增两项，各自与
   **SQL 原文**逐项比对（口径 F33）。一端是 `.sql` 里的文本，一端是 Python 的
   常量，**它们不会一起变**。
2. **失败分类真的分得开**：每一条失败路径走一遍，断它的 `failure_kind` 与
   HTTP 状态码。压成「上传失败」的代价见 `upload_failures.py` 那张表。
3. **先查后提示**：说得出是哪一份、什么时候传的；而唯一索引那条路
   （先查后插之间的窗口）**仍然在，仍然被测**——两层，不是一层换一层。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.events import REQUIRED_KEYS, UPLOAD_EVENT_TYPES, complete_event
from api.upload_failures import (
    FAILURE_KINDS,
    HTTP_STATUS_BY_KIND,
    RETRYABLE_KINDS,
    UploadFailure,
    classify_http_status,
)

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import row

REPO = Path(__file__).resolve().parents[2]
MIG_UPLOAD = REPO / "supabase" / "migrations" / "20260912180000_f3_upload_events.sql"
CORPUS = REPO / "实验脚本" / "测试集"
DECK = CORPUS / "MIT22_081JF10_lec01b.pdf"
COURSE = "cccccccc-3333-4333-8333-cccccccccccc"
SESSION = "99999999-9999-4999-8999-999999999999"


# ---------------------------------------------------------------- 1 · 镜像比对


def parse_failure_kinds_from_sql() -> list[str]:
    """从迁移原文里解析 `f3_deck_upload_failure_kinds()` 的那个 array[...]。

    解析不出来**抛**，不返回空列表：空列表会让下面的比对
    「两边都是空」地平凡通过（同 F16 反对的那件事）。
    """
    src = MIG_UPLOAD.read_text(encoding="utf-8")
    m = re.search(r"f3_deck_upload_failure_kinds\(\).*?select array\[(.*?)\]",
                  src, re.DOTALL)
    if not m:
        raise AssertionError("解析不出 f3_deck_upload_failure_kinds 的清单——"
                             "解析器与 SQL 写法漂了")
    kinds = re.findall(r"'([a-z_]+)'", m.group(1))
    if not kinds:
        raise AssertionError("解析到空清单")
    return kinds


def parse_required_keys_from_sql() -> dict[str, list[str]]:
    src = MIG_UPLOAD.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"when\s+'([a-z_]+)'\s*\n?\s*then\s+array\[(.*?)\]",
                         src, re.DOTALL):
        out[m.group(1)] = re.findall(r"'([a-z_]+)'", m.group(2))
    if not out:
        raise AssertionError("一个 when 分支都没解析出来")
    return out


class TestMirrorsTheSql:
    def test_failure_kinds_mirror_the_sql(self):
        assert list(FAILURE_KINDS) == parse_failure_kinds_from_sql()

    def test_parser_has_discriminating_power(self):
        """**先立解析器的分辨力**：它至少要认出 8 类。

        少了这一条，解析器只认出 1 类时上面的比对会因为「两边都只有 1 类」
        而平凡通过。
        """
        assert len(parse_failure_kinds_from_sql()) == 8

    def test_required_keys_for_both_upload_types_mirror_the_sql(self):
        """与**生效定义**比，不与本批那个迁移比。

        🔁 内测前-P1 改：原来比的是 `20260912180000` 里那份。`deck_uploaded` 的清单
        在 `20260915120000` 里少了一个键之后，那份已不生效——跟它比，
        Python 侧改对了会红、改错了（保持旧清单）反而绿。
        """
        from test_marks import _effective_required_keys

        parsed = _effective_required_keys()
        for t in UPLOAD_EVENT_TYPES:
            assert set(REQUIRED_KEYS[t]) == set(parsed[t]), (
                f"{t}：Python {sorted(REQUIRED_KEYS[t])} ≠ SQL {sorted(parsed[t])}")

    def test_all_eleven_types_survive_the_replace(self):
        """`f1_event_required_keys` 又被 `create or replace` 了一次 ——
        漏一个 `when` 分支，那个事件会当场变成「未知类型」全部被拒。

        十一个 = F1 五个 + 讲解四个 + 上传两个。
        """
        parsed = parse_required_keys_from_sql()
        assert len(parsed) == 11, sorted(parsed)
        for t in ("style_pair_shown", "style_pair_chosen", "style_onboarding_completed",
                  "style_text_mapped", "style_config_changed", "explain_turn",
                  "style_review_result", "regeneration_triggered", "retrieval_executed",
                  "deck_uploaded", "deck_upload_failed"):
            assert t in parsed, f"{t} 的 when 分支在 replace 之后没了"

    def test_page_count_is_not_required_on_failure(self):
        """⚠️ 口径 F50：失败时 `page_count` 往往是 null（连解析都没到）。

        进了必需清单的话，**格式错误这一类失败进不了库**——
        而那是用户第一次上传最可能撞上的一类。
        """
        parsed = parse_required_keys_from_sql()
        for key in ("page_count", "source_format", "duration_ms"):
            assert key not in parsed["deck_upload_failed"], key
            assert key not in REQUIRED_KEYS["deck_upload_failed"], key
        # 允许控制：成功那一侧**必须**有它们，否则「页数」这个 PRD §13.1 的
        # 硬要求就没有任何护栏。
        for key in ("page_count", "source_format", "duration_ms"):
            assert key in parsed["deck_uploaded"], key


class TestTheTaxonomyIsUsable:
    def test_every_kind_has_a_distinct_status(self):
        """**一个类一个码，不复用。** 复用会让前端按状态码分支时把两类并成一类。"""
        assert set(HTTP_STATUS_BY_KIND) == set(FAILURE_KINDS)
        codes = list(HTTP_STATUS_BY_KIND.values())
        assert len(set(codes)) == len(codes), f"状态码有重复：{codes}"

    def test_pptx_and_unsupported_are_different(self):
        """「还没做」与「不支持」**必须分得开**。

        压成一类的用户后果：他会以为 PPTX 永远不支持，去手动转 PDF。
        分析后果：§3.2 的「课件支持率」里，会随交付消失的那一半
        与不会消失的那一半混成一个数。
        """
        assert HTTP_STATUS_BY_KIND["pptx_not_implemented"] == 501
        assert HTTP_STATUS_BY_KIND["unsupported_format"] == 415

    def test_retryable_set_is_small_and_deliberate(self):
        """重试集合取小。**把可重试判成不可重试，用户少点一次按钮；
        反过来他会一直重试一个永不成功的上传。**"""
        assert RETRYABLE_KINDS == {"db_error", "unknown"}
        for kind in ("duplicate_in_course", "unsupported_format", "too_large",
                     "unparsable", "pptx_not_implemented", "empty_file"):
            assert kind not in RETRYABLE_KINDS

    def test_unknown_kind_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="未知的上传失败分类"):
            UploadFailure("unparseable", "拼错了一个字")   # 多一个 e

    def test_classify_from_status_roundtrips(self):
        for kind, code in HTTP_STATUS_BY_KIND.items():
            assert classify_http_status(code) == kind
        assert classify_http_status(418) == "unknown"


class TestCompleteEventTreatsZeroAsPresent:
    def test_zero_and_false_are_not_missing(self):
        """`skipped_pages` 常为 0、`retryable` 可能是 False。

        用真值判断（`if not payload.get(k)`）会把它们当成「缺字段」并抛，
        于是**一次完全正常的上传发不出事件**。这一条钉着那不是巧合。

        🔁 内测前-P1 改：原先这里用 `restricted_pages_excluded: 0` 做样本，
        钉的正是要被推翻的行为（那个 0 是假证据，口径 F69）。
        「0 不算缺」这条性质本身没变，换成仍必需且常为 0 的 `skipped_pages` 来钉。
        """
        ev = complete_event("deck_uploaded", {
            "filename": "a.pdf", "source_format": "pdf", "page_count": 3,
            "usable_page_count": 3, "verdict": "usable", "slice_count": 3,
            "embedded_count": 3, "skipped_pages": 0,
            "restricted_pages_excluded": None, "duration_ms": 12.5,
        })
        assert ev["payload"]["skipped_pages"] == 0
        assert "skipped_pages" in REQUIRED_KEYS["deck_uploaded"], (
            "样本字段不在必需清单里了——这条就不再测「0 不算缺」")

    def test_missing_required_still_raises(self):
        """允许控制：真缺字段仍然要抛。少了它，上一条在一个「什么都不检查」的
        实现上也绿。"""
        with pytest.raises(ValueError, match="failure_kind"):
            complete_event("deck_upload_failed", {"filename": "a.pdf", "bytes": 10})

    def test_unknown_event_type_raises_here_not_at_the_db(self):
        with pytest.raises(ValueError, match="未知 event_type"):
            complete_event("deck_bogus", {"a": 1})


# ---------------------------------------------------------------- 2/3 · 路由


class RecordingRest:
    """假 PostgREST。**记下写进 style_events 的行**，供事件断言。

    `existing_sha` 非空时，`/documents?...content_sha256=eq.<sha>` 这一查
    会返回一行 —— 用来触发「先查后提示」那条路。
    """

    def __init__(self, *, existing_sha: str | None = None,
                 fail_insert_document: bool = False) -> None:
        self.existing_sha = existing_sha
        self.fail_insert_document = fail_insert_document
        self.events: list[dict[str, Any]] = []
        self.seen: list[str] = []

    def __call__(self, url: str, headers: dict[str, str], body: Any = None) -> Any:
        self.seen.append(url)
        if body is not None:
            if "/style_events" in url:
                self.events.extend(body if isinstance(body, list) else [body])
                return None
            if "/documents" in url:
                if self.fail_insert_document:
                    from api.supabase import DuplicateDocument
                    raise DuplicateDocument("这门课里已经有同一份文件了")
                import uuid as _uuid
                return [{"id": str(_uuid.uuid4()), **(body if isinstance(body, dict) else {})}]
            return None
        # 读取
        if "/style_config" in url:
            return [row(USER_A)]
        if "/documents" in url and "content_sha256=eq." in url:
            if self.existing_sha and f"content_sha256=eq.{self.existing_sha}" in url:
                return [{"id": "dddddddd-1111-4111-8111-dddddddddddd",
                         "filename": "早先传的.pdf",
                         "created_at": "2026-09-01T10:00:00Z", "page_count": 39}]
            return []
        return []


class FakeEmbedder:
    name = "fake"
    dimensions = 1024

    def embed(self, texts):
        return [[0.0] * 1024 for _ in texts]


def client(jwks, rest: RecordingRest):
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
        embedder_factory=lambda: FakeEmbedder(),
    )
    return TestClient(app)


def post_file(c, keypair, *, name: str, data: bytes, session: str | None = SESSION):
    q = f"?session_id={session}" if session else ""
    return c.post(
        f"/courses/{COURSE}/documents{q}",
        headers={"Authorization": f"Bearer {keypair.sign()}"},
        files={"file": (name, data, "application/pdf")},
    )


def only_event(rest: RecordingRest) -> dict[str, Any]:
    assert len(rest.events) == 1, f"期望 1 条事件，得到 {len(rest.events)}"
    return rest.events[0]


class TestFailuresAreClassifiedNotCollapsed:
    """每一条失败路径走一遍。**这一组的价值在于它们互不相同。**"""

    CASES = [
        ("讲义.txt", b"x", 415, "unsupported_format"),
        ("讲义.pptx", b"x", 501, "pptx_not_implemented"),
        ("讲义.pdf", b"", 400, "empty_file"),
        ("讲义.pdf", b"not a pdf at all", 422, "unparsable"),
    ]

    @pytest.mark.parametrize("name,data,status,kind", CASES)
    def test_each_failure_gets_its_own_kind_and_status(self, jwks, keypair,
                                                       name, data, status, kind):
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name=name, data=data)
        assert r.status_code == status, r.text
        ev = only_event(rest)
        assert ev["event_type"] == "deck_upload_failed"
        assert ev["payload"]["failure_kind"] == kind
        assert ev["payload"]["http_status"] == status
        assert ev["payload"]["filename"] == name
        # **失败时 deck_id 是 null，且这是事实不是遗漏**：没有 document 行。
        assert ev["deck_id"] is None
        assert ev["course_id"] == COURSE

    def test_the_four_kinds_really_differ(self, jwks, keypair):
        """**把上面那组的分辨力单独立起来。**

        少了这一条，一个「所有失败都报 unsupported_format」的实现会让
        parametrize 里三分之一的用例绿，而读报告的人看到的是「大部分通过」。
        """
        kinds = []
        for name, data, _status, _kind in self.CASES:
            rest = RecordingRest()
            post_file(client(jwks, rest), keypair, name=name, data=data)
            kinds.append(only_event(rest)["payload"]["failure_kind"])
        assert len(set(kinds)) == len(kinds), kinds

    def test_too_large_is_its_own_kind(self, jwks, keypair):
        from parse_service.app import MAX_UPLOAD_BYTES

        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name="大.pdf",
                      data=b"x" * (MAX_UPLOAD_BYTES + 1))
        assert r.status_code == 413
        ev = only_event(rest)
        assert ev["payload"]["failure_kind"] == "too_large"
        assert ev["payload"]["bytes"] == MAX_UPLOAD_BYTES + 1

    def test_failure_payload_carries_no_exception_text(self, jwks, keypair):
        """⚠️ **异常原文不进事件**（PRD §12.2 / 口径 F32 的同一条）。

        `unparsable` 的原文来自解析器，而解析器谈论的是**用户上传的文件内容**。
        原文回给这一次请求的用户（HTTP body），不进分析数据。
        """
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name="坏.pdf", data=b"not a pdf at all")
        assert "无法处理该 PDF" in r.text          # 用户看得到
        blob = repr(only_event(rest)["payload"])
        assert "无法处理该 PDF" not in blob        # 事件里没有
        assert "cannot open" not in blob.lower()

    def test_retryable_flag_is_on_the_event(self, jwks, keypair):
        rest = RecordingRest()
        post_file(client(jwks, rest), keypair, name="讲义.txt", data=b"x")
        assert only_event(rest)["payload"]["retryable"] is False


class TestPreCheckThenTell:
    """「先查后提示」**与**唯一索引，两层都要。"""

    def test_pre_check_names_the_existing_document(self, jwks, keypair):
        """说得出是哪一份、什么时候传的 —— 这才叫「提示」。"""
        import hashlib

        data = DECK.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        rest = RecordingRest(existing_sha=sha)
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=data)
        assert r.status_code == 409, r.text
        ev = only_event(rest)
        assert ev["payload"]["failure_kind"] == "duplicate_in_course"
        # **哪一层拦下的要记**：如果 by unique_index 长期占多数，
        # 说明「先查」这一层没在起作用，而它的绿是看不见的。
        assert ev["payload"]["caught_by"] == "pre_check"
        assert ev["payload"]["existing_document_id"] == "dddddddd-1111-4111-8111-dddddddddddd"

    def test_sha_we_look_up_is_the_one_parse_produces(self, jwks, keypair):
        """**判据的两端**：我们查的那个 sha 必须就是 `parse_pdf` 落库的那个。

        不等的话「重复检测永远查不到」，而**没有任何东西会红**——
        上一条会以为它测的是「查不到就放行」。
        这里用 `hashlib` 独立算一次（第三个来源），三者必须相等。
        """
        import hashlib

        from parse_service.pdf import parse_pdf

        data = DECK.read_bytes()
        assert parse_pdf(data, DECK.name).document.content_sha256 == \
            hashlib.sha256(data).hexdigest()

    def test_unique_index_path_still_works(self, jwks, keypair):
        """**先查后插之间那个窗口**：两个标签页同时传同一份文件。

        `existing_sha=None`（先查查不到）+ insert 抛 23505 → 仍然 409。
        少了这一条，「先查」会被当成唯一的一层，而它挡不住并发。
        """
        rest = RecordingRest(existing_sha=None, fail_insert_document=True)
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert r.status_code == 409, r.text
        ev = only_event(rest)
        assert ev["payload"]["failure_kind"] == "duplicate_in_course"
        assert ev["payload"]["caught_by"] == "unique_index"
        assert ev["payload"]["existing_document_id"] is None

    def test_no_duplicate_passes_through(self, jwks, keypair):
        """允许控制：不重复时必须传得进去。

        少了它，一个「任何上传都报重复」的实现也能让上面三条全绿。
        """
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert r.status_code == 201, r.text


class TestSuccessEvent:
    def test_deck_uploaded_fills_deck_id(self, jwks, keypair):
        """🔑 **`deck_id` 那一列第一个真正的填写者。**

        `20260912120000_f3_explain_events.sql` 里登记着它「本阶段恒为 NULL」，
        触发条件是上传页。这一条就是那条登记被解掉的证据。
        """
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert r.status_code == 201
        ev = only_event(rest)
        assert ev["event_type"] == "deck_uploaded"
        assert ev["deck_id"] == r.json()["document_id"], "deck_id 与返回的 document_id 不一致"
        assert ev["deck_id"] is not None
        assert ev["course_id"] == COURSE

    def test_payload_has_what_prd_13_1_asks_for(self, jwks, keypair):
        """PRD §13.1 第一行：**文件类型、页数、处理耗时、成功/失败**。"""
        rest = RecordingRest()
        post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        pl = only_event(rest)["payload"]
        assert pl["source_format"] == "pdf"
        assert pl["page_count"] > 0
        assert pl["usable_page_count"] > 0
        assert pl["duration_ms"] >= 0
        # 「成功/失败」由 event_type 承担，**不另立布尔字段**：
        # 立了就有两处出处，漂开的表现是「deck_uploaded 里写着 ok=false」。
        assert not any(k in pl for k in ("ok", "success", "succeeded"))

    def test_slice_and_embedded_counts_are_both_there(self, jwks, keypair):
        """两个都要：不等就意味着有切片没拿到向量，而那种切片在检索里等于不存在。"""
        rest = RecordingRest()
        post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        pl = only_event(rest)["payload"]
        assert pl["slice_count"] > 0
        assert pl["embedded_count"] == pl["slice_count"]


class TestSessionIdDiscipline:
    def test_no_session_id_means_no_event_but_upload_succeeds(self, jwks, keypair):
        """**不现编 session_id**（同 `/explain`）。

        而上传本身**必须照常成功**——一条埋点不该让用户传不上课件（口径 F42）。
        """
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name=DECK.name,
                      data=DECK.read_bytes(), session=None)
        assert r.status_code == 201, r.text
        assert rest.events == []

    def test_event_write_failure_does_not_break_the_upload(self, jwks, keypair):
        """埋点写不进去时上传仍然 201。

        用户等了几十秒，不该因为一条事件写不进去而丢掉这次上传。
        """
        class Exploding(RecordingRest):
            def __call__(self, url, headers, body=None):
                if body is not None and "/style_events" in url:
                    raise RuntimeError("数据库返回 503")
                return super().__call__(url, headers, body)

        rest = Exploding()
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert r.status_code == 201, r.text
        assert rest.events == []

    def test_missing_style_row_does_not_break_the_upload(self, jwks, keypair):
        """**读不到 `style_config` 时上传仍然成功。**

        `arm` 要从那一行取（表上有 `arm = f1_assign_arm(user_id)` 的 CHECK）。
        最省事的写法是让上传路由依赖 `style_row`，**但那会把一次埋点的前置
        变成上传本身的前置**：0 行是坏状态（口径 F09），而**上传压根不需要风格配置**。
        """
        class NoStyleRow(RecordingRest):
            def __call__(self, url, headers, body=None):
                if body is None and "/style_config" in url:
                    return []      # 0 行 —— 坏状态
                return super().__call__(url, headers, body)

        rest = NoStyleRow()
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert r.status_code == 201, r.text
        assert rest.events == []


class TestOnlyOneDuplicateDocumentClass:
    """**同名同义的两个异常类，比两个不同名的更危险。**

    读代码的人看到 `DuplicateDocument` 不会去确认它是哪一个，而 `except`
    只认对象的真实类型。2026-09-12 这件事当场兑现：「先查后提示」在 `ingest.py`
    抛它自己那个，路由 `except` 的是 `supabase.py` 那个，两者无继承关系，
    于是那条重复提示掉进兜底的 `except Exception`，被报成 **422「无法处理该 PDF」**。
    """

    def test_both_import_paths_are_the_same_object(self):
        from api.ingest import DuplicateDocument as FromIngest
        from api.supabase import DuplicateDocument as FromSupabase

        assert FromIngest is FromSupabase, (
            "又变成两个类了。两个同名同义的异常，except 只会接住其中一个。")

    def test_it_is_defined_only_in_supabase_py(self):
        """**定义处只有一个。** 断的是源码里 `class DuplicateDocument` 出现几次——
        `is` 那条在「ingest 重新定义了一个恰好也叫这个名字的子类」时仍然可能绿。
        """
        import re
        from pathlib import Path

        api_dir = Path(__file__).resolve().parents[1] / "api"
        defs = {f.name: len(re.findall(r"^class DuplicateDocument\b", f.read_text(encoding="utf-8"),
                                      re.MULTILINE))
                for f in sorted(api_dir.glob("*.py"))}
        assert sum(defs.values()) == 1, f"定义处不止一个：{ {k: v for k, v in defs.items() if v} }"
        assert defs["supabase.py"] == 1, defs

    def test_it_carries_a_409(self):
        """允许控制：合并之后它仍然是 409，不是 502。

        少了它，一个「把它合并成普通 SupabaseError」的改动会让上面两条全绿，
        而重复上传会返回 502。
        """
        from api.supabase import DuplicateDocument, SupabaseError

        e = DuplicateDocument("这门课里已经有同一份文件了")
        assert isinstance(e, SupabaseError) and e.status == 409
        assert e.existing is None


class TestErrorResponseShape:
    """前端按**分类**分支，所以 `failure_kind` 必须在响应体里。"""

    def test_failure_kind_is_a_sibling_and_detail_stays_a_string(self, jwks, keypair):
        """⚠️ **`detail` 保持字符串，`failure_kind` 是同级字段。**

        把 `detail` 换成对象会让既有读取方当场变红 ——
        `api/scripts/ingest_real.py` 里那条「409 且是人话，不是 23505」
        断的是 `isinstance(detail, str)`。而那条断言仍然要成立。
        """
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name="讲义.pptx", data=b"x")
        body = r.json()
        assert r.status_code == 501
        assert isinstance(body["detail"], str), "detail 变成对象了，会打断既有读取方"
        assert body["failure_kind"] == "pptx_not_implemented"
        assert body["retryable"] is False

    def test_every_kind_reaches_the_client_as_its_own_kind(self, jwks, keypair):
        """八类里能从路由走到的那几类，`failure_kind` 都要原样到前端。

        **从状态码反推会丢信息**（415 与 501 都是「格式家族」），
        所以主路径必须是响应体里那个字段。
        """
        seen = {}
        for name, data, kind in (("a.txt", b"x", "unsupported_format"),
                                 ("a.pptx", b"x", "pptx_not_implemented"),
                                 ("a.pdf", b"", "empty_file"),
                                 ("a.pdf", b"junk", "unparsable")):
            rest = RecordingRest()
            r = post_file(client(jwks, rest), keypair, name=name, data=data)
            seen[kind] = r.json().get("failure_kind")
        assert seen == {k: k for k in seen}, seen

    def test_duplicate_detail_is_human_not_a_pg_code(self, jwks, keypair):
        """409 的 `detail` 里不许出现 23505（`ingest_real.py` 断的就是这个）。"""
        import hashlib

        data = DECK.read_bytes()
        rest = RecordingRest(existing_sha=hashlib.sha256(data).hexdigest())
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=data)
        assert r.status_code == 409
        assert "23505" not in r.json()["detail"]
        assert r.json()["failure_kind"] == "duplicate_in_course"


class TestEventResultIsVisibleFromOutside:
    """**埋点结果进响应。** 2026-09-13 推翻了上一批自己的决定。

    上一批写的是「日志够了，上传是低频动作」。代价当场兑现：人工验收想确认
    `deck_uploaded` 的 `deck_id` 到底是不是 null，而**从外面无从确认**——
    客户端对 `style_events` 只有 insert 权限（读事件走 service_role，
    而验收脚本刻意不持有它）。于是只能去 SQL Editor 手查，
    一次查错地方（`payload->>'deck_id'` —— 它其实是**列**）就读成了「功能没做」。

    `/explain` 从一开始就把 `events` 放进响应，而那正是「四个讲解事件真的落库」
    当初能被验证的唯一途径。**拿可验证性换页面简洁，是拿一个响亮的失败换一个安静的。**
    """

    def test_success_response_carries_the_deck_id_that_was_written(self, jwks, keypair):
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert r.status_code == 201
        ev = r.json()["events"]
        assert ev["emitted"] == 1 and ev["failed"] == 0
        assert ev["event_type"] == "deck_uploaded"
        # **响应里那个 deck_id 必须等于真的写进去的那一个**，
        # 而不是「我打算写的那一个」——两者相等是靠 only_event 那边比出来的。
        assert ev["deck_id"] == r.json()["document_id"]
        assert only_event(rest)["deck_id"] == ev["deck_id"]

    def test_failure_response_carries_its_event_result(self, jwks, keypair):
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name="讲义.txt", data=b"x")
        ev = r.json()["events"]
        assert ev["emitted"] == 1 and ev["failed"] == 0
        assert ev["event_type"] == "deck_upload_failed"
        # 失败时 deck_id 是 null，**而这是事实不是遗漏**：没有 document 行。
        assert ev["deck_id"] is None

    def test_skipped_reason_is_visible_too(self, jwks, keypair):
        """没有 `session_id` 时，响应要说得出**为什么没发**。

        少了它，「0 条事件」与「发了但写失败」在响应里长得一样。
        """
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name=DECK.name,
                      data=DECK.read_bytes(), session=None)
        assert r.status_code == 201
        assert "session_id" in r.json()["events"]["skipped_reason"]

    def test_write_failure_is_reported_in_the_response(self, jwks, keypair):
        """埋点写失败时上传仍 201，**但响应里说得出它失败了**。"""
        class Exploding(RecordingRest):
            def __call__(self, url, headers, body=None):
                if body is not None and "/style_events" in url:
                    raise RuntimeError("数据库返回 503")
                return super().__call__(url, headers, body)

        rest = Exploding()
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert r.status_code == 201
        ev = r.json()["events"]
        assert ev["emitted"] == 0 and ev["failed"] == 1
        assert "503" in str(ev.get("failures"))


class TestFieldsAreWhereTheDocsSayTheyAre:
    """**字段落在哪，要能被断言，不能靠猜。**

    2026-09-13 人工验收查 `payload->>'deck_id'` 与 `payload->>'reason'`，
    两个都 null，读成「功能没做」。真实情况：
      · `deck_id` 是**表的一列**，不在 payload 里；
      · `reason` **不是本项目任何事件的字段** —— 失败分类叫 `failure_kind`。

    这一组把「在哪」钉成断言，于是下一次不必猜。
    """

    COLUMNS = {"user_id", "event_type", "arm", "session_id", "payload",
               "course_id", "deck_id"}

    def test_deck_id_is_a_column_not_a_payload_key(self, jwks, keypair):
        rest = RecordingRest()
        post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        ev = only_event(rest)
        assert "deck_id" in ev, "deck_id 不在列里"
        assert "deck_id" not in ev["payload"], (
            "deck_id 同时出现在 payload 里 —— 两处出处，迟早漂")

    def test_failure_kind_is_a_payload_key_not_a_column(self, jwks, keypair):
        rest = RecordingRest()
        post_file(client(jwks, rest), keypair, name="讲义.txt", data=b"x")
        ev = only_event(rest)
        assert ev["payload"]["failure_kind"] == "unsupported_format"
        assert "failure_kind" not in set(ev) - {"payload"}

    def test_there_is_no_field_called_reason(self, jwks, keypair):
        """**`reason` 不是字段名。** 这一条把那次误查钉下来。

        它不是为了证明「我对」，是为了让下一个查的人在断言里看到真名。
        """
        rest = RecordingRest()
        post_file(client(jwks, rest), keypair, name="讲义.txt", data=b"x")
        ev = only_event(rest)
        assert "reason" not in ev["payload"], "多了一个 reason —— 那就是两处出处"
        assert "failure_kind" in ev["payload"], "分类字段的真名是 failure_kind"

    def test_row_keys_are_exactly_the_columns(self, jwks, keypair):
        """行上的键**恰好**是那几列。多一个 = 会被 PostgREST 以 PGRST204 拒掉。"""
        rest = RecordingRest()
        post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert set(only_event(rest)) == self.COLUMNS

    def test_table_comment_names_both_places(self):
        """表注释里必须写清「哪些是列、哪些在 payload」+ 两个真名。

        猜字段名这件事可以被消除，而消除它的地方就是表自己
        （`\\d+ style_events` 看得到）。
        """
        from pathlib import Path

        mig = (Path(__file__).resolve().parents[2] / "supabase" / "migrations"
               / "20260913090000_f3_deck_id_invariant.sql").read_text(encoding="utf-8")
        assert "comment on table public.style_events" in mig
        for token in ("failure_kind", "caught_by", "deck_id", "不叫 reason"):
            assert token in mig, token


class TestRestrictedPagesExcludedIsNull:
    """内测前-P1 · `restricted_pages_excluded` **停止说谎**（口径 F68 / F69）。

    此前每次成功上传都写 0。那个 0 看起来是「检查过，没有受限页」，
    实际是「从来没检查过」——`/upload` 从没给 `ingest_document` 传过受限页清单。
    而表里分不出这两者。**null 的语义正是「未检查」。**

    三条守卫，各配一次「改回去就红」（执行记录里逐条记了怎么让它红的）：
    ① 写入侧是 null；② 它不在必需清单里（加回去 → 一条 null 的正常事件被拒）；
    ③ 其余必需字段照旧（去掉任一 → 红）。
    `test_ingest.py::TestRestrictedPages` **不动**：它测函数本身，函数没坏，
    坏的是没人给它传参。
    """

    def test_upload_writes_null_not_zero(self, jwks, keypair):
        """守卫 ① · **断的是写进表里的那一行**，不是函数返回值。"""
        rest = RecordingRest()
        r = post_file(client(jwks, rest), keypair, name=DECK.name, data=DECK.read_bytes())
        assert r.status_code == 201, r.text
        pl = only_event(rest)["payload"]
        # 键**在**、值是 null。键不在的话「未检查」与「字段漏采」又分不开了。
        assert "restricted_pages_excluded" in pl
        assert pl["restricted_pages_excluded"] is None, (
            f"写成了 {pl['restricted_pages_excluded']!r} —— 一个看起来「检查过」的假证据")
        # 响应里也不许是 0：上传页读的是响应，同一个谎不能换个地方说。
        assert "restricted_pages_excluded" in r.json()
        assert r.json()["restricted_pages_excluded"] is None

    def test_it_is_not_in_the_required_list(self):
        """守卫 ② · Python 镜像与 SQL 生效定义**都**不含它（口径 F50）。"""
        from test_marks import _effective_required_keys

        assert "restricted_pages_excluded" not in REQUIRED_KEYS["deck_uploaded"]
        assert "restricted_pages_excluded" not in _effective_required_keys()["deck_uploaded"]

    def test_a_null_event_is_accepted(self):
        """允许控制：值为 null 的正常事件构造得出来（不抛）。"""
        ev = complete_event("deck_uploaded", {
            "filename": "a.pdf", "source_format": "pdf", "page_count": 3,
            "usable_page_count": 3, "verdict": "usable", "slice_count": 3,
            "embedded_count": 3, "skipped_pages": 0,
            "restricted_pages_excluded": None, "duration_ms": 12.5,
        })
        assert ev["payload"]["restricted_pages_excluded"] is None

    def test_reverse_control_putting_it_back_rejects_the_null_event(self, monkeypatch):
        """**常驻反向控制**：把它加回清单 → 上面那条 null 的正常事件必须被拒。

        少了这一条，「不在清单里」那条的绿可能只是因为清单检查本身失效了。
        数据库那一层的同一件事是 `verify_rls_local.mjs` 的 R20。
        """
        import api.events as events

        patched = dict(events.REQUIRED_KEYS)
        patched["deck_uploaded"] = (*events.REQUIRED_KEYS["deck_uploaded"],
                                    "restricted_pages_excluded")
        monkeypatch.setattr(events, "REQUIRED_KEYS", patched)
        with pytest.raises(ValueError, match="restricted_pages_excluded"):
            events.complete_event("deck_uploaded", {
                "filename": "a.pdf", "source_format": "pdf", "page_count": 3,
                "usable_page_count": 3, "verdict": "usable", "slice_count": 3,
                "embedded_count": 3, "skipped_pages": 0,
                "restricted_pages_excluded": None, "duration_ms": 12.5,
            })

    def test_every_other_required_field_is_still_required(self):
        """守卫 ③ · 其余九个各缺一次都必须抛。先立分辨力：恰好九个。"""
        full = {
            "filename": "a.pdf", "source_format": "pdf", "page_count": 3,
            "usable_page_count": 3, "verdict": "usable", "slice_count": 3,
            "embedded_count": 3, "skipped_pages": 0, "duration_ms": 12.5,
            "restricted_pages_excluded": None,
        }
        keys = REQUIRED_KEYS["deck_uploaded"]
        assert len(keys) == 9, keys
        for k in keys:
            partial = {kk: v for kk, v in full.items() if kk != k}
            with pytest.raises(ValueError, match=k):
                complete_event("deck_uploaded", partial)
