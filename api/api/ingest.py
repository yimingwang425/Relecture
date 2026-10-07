"""摄入：上传 → 解析 → 切片 → 组文本 → 嵌入 → 落库。

**接线前这条链一行都没有。** `parse-service` 有 `/parse` 但无鉴权、不落盘；
`retrieval/corpus.py` 文件头写着「不写盘、不碰数据库」——它读的是仓库里的测试集。

## 一次事务性的动作，拆不开

`compose()` 的 boilerplate 检测是**整份文档范围**的（要看所有页才知道哪些块是页眉页脚），
所以嵌入必须**整份一起算**，不能增量。这决定了摄入的边界就是「一份文档」。

## 受限页在**索引侧**也排除，不只在评测集侧

`retrieval/corpus.py` 的原话：「只在评测集侧排除属于『靠约定保证』，靠不住。」
同一条在生产上更硬——第三方保留权利的页**不入库**，不是「入库但不检索」。
⚠️ 当前 `RESTRICTED_PAGES` 是按测试集文件名写死的清单（`SOURCES.md` 为准）。
真实用户上传的课件**不在那张清单上**，所以这一层对他们恒不触发：
**如实登记，不写成「受限页已排除」**——那是对测试语料成立的句子。

## 嵌入是唯一要出境的一步

切片原文 → OpenAI（PRD §13.3 的**第二次出境**，且处理地不在新加坡）。
所以 `embedder` 是注入的：断言里喂一个确定性的假嵌入器，
**真解析、真切片、假嵌入**——省掉的只有那次网络调用与那笔钱，
解析与切片走的是生产的同一份代码。

## 2026-10-07 加了三步（口径 F99 / F100）

    解析 → 查重 → **图片页转写** → 切片 → **定位语** → 嵌入 + **词条** → 落库

- **图片页转写**（`explain.rag.VisionTranscriber`）：`image_only` / `scanned_ocr` 页
  渲染成 PNG 交给 Haiku 转写，转写成功的页变成切片（`slice_id = v###`，标题写明
  「图像转写，可能有误」）。**这是 PRD §13.3 的又一次出境**：页面图像 → Anthropic。
- **定位语**（`explain.rag.Contextualizer`）：每片一两句「在整份课件里的位置」，
  进嵌入文本（前缀）与词条。英文课件的定位语带中文括注——中文提问在英文课件上
  唯一能字面命中的东西。
- **词条**（`slice_lexicon`）：混合检索的词法一路。

三步**都不让上传失败**：模型调用失败或没配 key 时，转写那页照旧是跳过页、
定位语为空、嵌入文本就是组文本本身。结果计数进 `IngestResult`，进上传事件。

查重提到了解析之后、转写之前：转写要花钱，重复上传的那一份不该花。

决策日期：2026-09-12 · 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = ["Embedder", "IngestResult", "DuplicateDocument", "UnusableDocument",
           "ingest_document", "EMBED_MODEL_ID"]


class UnusableDocument(Exception):
    """整份课件一页都读不出文字（`verdict == "unusable"`）——**不建行、不入库**。

    ## 为什么这条判断在这里，不在 parse-service

    `parse-service/README.md` 的 P08 写得很清楚：文档级 verdict 只报事实
    （`usable_page_count == 0` → `unusable`），**放行并告知，不替用户拒绝**——
    「可用页占比低于某数就拒收」是把产品决策塞进解析层。
    所以「拒不拒收」这个产品决策就该落在调用方，也就是这里。

    ## 不拦的后果（2026-09-20 用户实测报上来的）

    不拦的话这份文档照样建行、照样进课件列表，界面上写着
    「已入库：0 个切片 · 0 条向量 · 19 页读不出文字 · 整份判为不可用」——
    **「已入库」和「不可用」同时为真**。它之后会一直挂在那门课里：
    检索一片都取不到，而用户看到的是一份「传上去了」的课件。
    更糟的是唯一索引把它记住了：同一份文件再传一次会被判
    `duplicate_in_course`（「这门课里已经有了」），于是他连重试都做不到。

    ⚠️ **判据是 verdict，不是切片数。** 切片数为 0 还有别的来路——
    比如受限页把所有片都排除掉（`restricted_pages`）。那是另一件事，
    文案与处置都不同，混在一起用户会被告知错误的原因。
    """

    def __init__(self, message: str, *, page_count: int,
                 unusable_page_count: int, reason: str) -> None:
        super().__init__(message)
        self.message = message
        self.page_count = page_count
        self.unusable_page_count = unusable_page_count
        #: 解析器给的原因（「共 19 页：2 页 image_only、17 页 scanned_ocr」）。
        #: **它进 HTTP body 给这一次请求的用户，不进事件**——同 `unparsable`
        #: 那条纪律：它谈论的是用户上传的文件内容（口径 F32 / PRD §12.2）。
        self.reason = reason


class Embedder(Protocol):
    """把一批文本变成一批向量。维度必须等于 `slice_embeddings.embedding` 的 1024。

    ⚠️ **返回类型写的是 `Sequence[Sequence[float]]`，不是 `list[list[float]]`。**
    生产的那个实现（`retrieval.embed.OpenAIEmbedder`）返回的是 **`np.ndarray`**。
    写成 `list[list[float]]` 的后果不是类型标注不准，是**下游真的会炸**：
    `if vectors:` 对一个多元素 ndarray 抛
    `ValueError: The truth value of an array with more than one element is ambiguous`。

    这一条是第一次真跑摄入时抓出来的（2026-09-12），
    **与 F16 是同一个形态**：协议声明、所有测试替身、以及依赖它的下游代码
    三者都来自「我写下的那个类型」，**没有一端来自生产实现真的返回什么**。
    于是 46 条 pytest 全绿，而这条链第一次碰到真嵌入器就 422。

    处置有两层：① 本模块在边界上**归一化成纯 Python list**（见 `ingest_document`），
    下游从此与类型无关；② `tests/test_ingest.py` 里有一个**返回 ndarray** 的假嵌入器，
    它照着生产实现的返回类型写，并有一条断言钉着「生产实现确实返回 ndarray」——
    生产哪天改了返回类型，那条会红，而不是让假货静静地停在旧类型上。
    """

    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> Sequence[Sequence[float]]: ...


# ⚠️ **`DuplicateDocument` 不在本模块定义。** 唯一定义处是 `api/api/supabase.py`
# （2026-09-12 合并，理由见那里的 docstring）：此前两个模块各有一个**同名同义**的类，
# 而路由 `except` 的是其中一个、「先查后提示」抛的是另一个 —— 那条重复提示因此
# 掉进兜底的 `except Exception`，被报成 422「无法处理该 PDF」。
from .supabase import DuplicateDocument  # noqa: E402  —— 放在这里是为了紧挨上面那段说明

@dataclass(frozen=True, slots=True)
class IngestResult:
    document_id: str
    slice_count: int
    embedded_count: int
    skipped_pages: int
    restricted_pages_excluded: int
    verdict: str
    # ---- PRD §13.1 第一行要的「文件类型、页数」。**从 parse 的产出带上来，
    # 不让调用方再解析一次**：路由要发 `deck_uploaded` 事件，而它手里只有字节。
    # 调用方自己去读一遍 PDF 就是第二处出处（F13），且那份可能与落库的不一致。
    source_format: str = ""
    page_count: int = 0
    usable_page_count: int = 0
    # ---- 2026-10-07（口径 F99 / F100）
    index_ruleset: str = ""
    vision_page_count: int = 0
    #: 候选页超过上限、没送去转写的页数。**非 0 要说出来**，否则用户以为整份都读了。
    vision_skipped_over_cap: int = 0
    contextualized_count: int = 0
    #: 转写 + 定位语的模型成本。None = 有一次超时（可能已计费，金额未知，F73 同一条）。
    llm_cost_usd: float | None = 0.0


#: 一份课件最多转写多少页。**是成本上限，不是质量口径**：Haiku 4.5 看一页图约
#: 1.6k 输入 token + 几百输出 token，≈ $0.005 / 页，40 页 ≈ $0.2 / 份。
#: 超出的页照旧记为跳过页，数量进 `vision_skipped_over_cap`。
VISION_MAX_PAGES = 40

#: 送去转写的那几类页。`empty` 不送——空白页没什么可转写的，送了只是花钱。
VISION_PAGE_KINDS = ("image_only", "scanned_ocr")

#: 渲染时的长边像素。Anthropic 文档：长边超过 1568 px 的图会被先缩小再看，
#: 渲染得更大只是多传字节、不多给信息。
VISION_LONG_EDGE_PX = 1568


def _render_pages(data: bytes, page_indices: list[int]) -> list[tuple[int, bytes]]:
    """把指定页（1 起）渲染成 PNG。"""
    import pymupdf

    out: list[tuple[int, bytes]] = []
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        for idx in page_indices:
            page = doc[idx - 1]
            long_edge = max(page.rect.width, page.rect.height) or 1.0
            zoom = VISION_LONG_EDGE_PX / long_edge
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            out.append((idx, pix.tobytes("png")))
    finally:
        doc.close()
    return out


#: 写进 `slice_embeddings.model` 的字符串。**从 `retrieval.selection` 读，不写字面量**——
#: 那是维度与模型这两个数在代码里的唯一出处（口径 F13 的同一条）。
def EMBED_MODEL_ID() -> str:  # noqa: N802 —— 它是个取值，不是类
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL

    return f"{SELECTED_MODEL}@{SELECTED_DIMENSIONS}"


def ingest_document(
    *,
    data: bytes,
    filename: str,
    owner: str,
    course_id: str,
    token: str,
    store: Any,
    embedder: Embedder,
    restricted_pages: frozenset[int] = frozenset(),
    split_threshold_chars: int | None = None,
    find_existing: Any | None = None,
    contextualizer: Any | None = None,
    vision: Any | None = None,
) -> IngestResult:
    """跑完整条链，返回落库结果。

    `store` 是 `SupabaseClient`——所有写入带**用户自己的 JWT**，RLS 由数据库执行
    （与 `/me/style` 同一条路径，服务端不持有 service_role key，口径 F40）。
    """
    from parse_service.pdf import parse_pdf
    from retrieval.compose import compose

    from explain.rag import VISION_PROMPT_VERSION
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_VARIANT
    from slicer import slice_document
    from slicer.schema import SLICE_SCHEMA_VERSION

    from .indexing import INDEX_RULESET, embedding_text, lexicon_text

    parsed = parse_pdf(data, filename)

    # ---- 「先查后提示」：建行之前问一次这门课里有没有同一份文件。
    #
    # ⚠️ **这一层不替代唯一索引。** 查完到插入之间有个窗口，两个标签页同时传
    # 同一份文件仍然会撞上 23505 → 409（`insert_document` 那条翻译还在）。
    #
    # ⚠️ **在 parse 之后查**：`content_sha256` 的出处是 `parse_pdf`，自己再算一份
    # 就是第二处出处（F13）。2026-10-07 起**排在转写之前**：转写要花钱，
    # 重复上传的那一份不该花。整份读不出字的文档从不入库，所以它不可能是「重复」，
    # 这一挪不改变任何既有分类。
    if find_existing is not None:
        existing = find_existing(parsed.document.content_sha256)
        if existing is not None:
            raise DuplicateDocument(
                "这门课里已经有同一份文件了", existing=existing)

    # ---- 图片页转写（口径 F100）。失败不让上传失败：那一页照旧是跳过页。
    # ⚠️ **受限页在送出去之前就排除**，不是转写完再丢：转写意味着页面图像已经出境
    # （PRD §13.3），事后丢掉结果不能撤回那次出境。
    candidates = [p.index for p in parsed.document.unusable_pages
                  if p.kind in VISION_PAGE_KINDS and p.index not in restricted_pages]
    over_cap = max(0, len(candidates) - VISION_MAX_PAGES)
    vision_hits: list[Any] = []
    llm_cost: float | None = 0.0
    if vision is not None and candidates:
        results = vision.transcribe_many(filename, _render_pages(data, candidates[:VISION_MAX_PAGES]))
        for r in results:
            if r.cost_usd is None:
                llm_cost = None
            elif llm_cost is not None:
                llm_cost += r.cost_usd
        vision_hits = [r for r in results if r.text]

    # ---- 一页都读不出文字、转写也没救回任何一页 → **到此为止，不建行**（见 `UnusableDocument`）。
    # 没有可用页就没有片可切，再往下走只是在为一份注定检索不到的文档建行、写唯一索引，
    # 把「重新传一份文字版」这条路也堵死。
    if parsed.document.verdict == "unusable" and not vision_hits:
        raise UnusableDocument(
            "这份课件一页都读不出文字，没有入库",
            page_count=parsed.document.page_count,
            unusable_page_count=len(parsed.document.unusable_pages),
            reason=parsed.document.verdict_reason,
        )

    sliceset = slice_document(parsed, split_threshold_chars)
    manifest = sliceset.manifest

    # 受限页不入索引（见模块 docstring 的登记）。
    kept = [s for s in sliceset.slices if not any(p in restricted_pages for p in s.page_indices)]
    excluded = len(sliceset.slices) - len(kept)

    # 转写出来的页 → 切片行。`v###` 前缀与 slicer 的 `s###` 分开，永不撞号。
    next_ordinal = max((s.ordinal for s in kept), default=0) + 1
    vision_rows = [
        {
            "slice_id": f"v{r.page_index:03d}",
            "ordinal": next_ordinal + i,
            "page_indices": [r.page_index],
            # **标题写明这是转写**：讲解在片头看得到它（`build_user_turn`），
            # 模型与学生都知道这一片不是课件原文。
            "title": f"第 {r.page_index} 页（图像转写，可能有误）",
            "text": r.text,
        }
        for i, r in enumerate(vision_hits)
    ]

    # ---- 定位语（口径 F99）。失败的那几片定位语为空，嵌入文本就是组文本本身。
    contexts: dict[str, str] = {}
    if contextualizer is not None and (kept or vision_rows):
        ctx_result = contextualizer.contextualize(filename, [
            *[(s.slice_id, list(s.page_indices), s.title, s.text) for s in kept],
            *[(v["slice_id"], v["page_indices"], v["title"], v["text"]) for v in vision_rows],
        ])
        contexts = ctx_result.contexts
        if ctx_result.cost_usd is None:
            llm_cost = None
        elif llm_cost is not None:
            llm_cost += ctx_result.cost_usd

    doc = store.insert_document(
        token=token,
        row={
            "owner": owner,
            "course_id": course_id,
            "filename": filename,
            "source_format": parsed.document.source_format,
            "content_sha256": parsed.document.content_sha256,
            "page_count": parsed.document.page_count,
            "usable_page_count": parsed.document.usable_page_count,
            "unusable_pages": [p.model_dump(mode="json") for p in parsed.document.unusable_pages],
            "verdict": parsed.document.verdict,
            "parser": parsed.document.parser.model_dump(mode="json"),
            # ---- 五个口径版本列。漏一个数据库就拒（not null），这是刻意的：
            # 口径一改新旧行再也分不开，而合并会得到一个看起来正常的错数。
            "parse_schema_version": parsed.schema_version,
            "slice_schema_version": SLICE_SCHEMA_VERSION,
            "slicer_version": manifest.slicer_version,
            "slice_ruleset_version": manifest.slice_ruleset_version,
            # NULL 有含义（= 不拆），所以原样传 None，不填 0
            "split_threshold_chars": split_threshold_chars,
            # 第六个口径版本列（口径 F97）：检索按它取这份文档的向量与词条。
            "index_ruleset": INDEX_RULESET,
            # 转写救回的页（口径 F100）。**不改 verdict / unusable_pages**——那是解析器的结论。
            "vision_pages": [
                {"index": r.page_index, "chars": len(r.text), "prompt": VISION_PROMPT_VERSION}
                for r in vision_hits],
        },
    )
    document_id = doc["id"]

    if kept or vision_rows:
        store.insert_slices(
            token=token,
            rows=[
                *[{
                    "document_id": document_id,
                    "slice_id": s.slice_id,
                    "owner": owner,
                    "ordinal": s.ordinal,
                    "page_indices": list(s.page_indices),
                    "title": s.title,
                    "text": s.text,
                    "char_count": s.char_count,
                    "units": [u.model_dump(mode="json") for u in s.units],
                    "images": [i.model_dump(mode="json") for i in s.images],
                    "page_signals": [p.model_dump(mode="json") for p in s.page_signals],
                    "prev_slice_id": s.prev_slice_id,
                    "next_slice_id": s.next_slice_id,
                } for s in kept],
                *[{
                    "document_id": document_id,
                    "owner": owner,
                    **v,
                    "char_count": len(v["text"]),
                    "units": [], "images": [], "page_signals": [],
                    "prev_slice_id": None, "next_slice_id": None,
                } for v in vision_rows],
            ],
        )

        # 组文本走 T0 变体（`retrieval.selection` 是那个选择的唯一出处）。
        # ⚠️ compose 要**整份** sliceset —— boilerplate 检测是文档范围的。
        # 转写片不在 sliceset 里（slicer 不认识它们），组文本就是转写原文。
        texts = compose(sliceset, SELECTED_VARIANT) if kept else {}
        ids = [s.slice_id for s in kept] + [v["slice_id"] for v in vision_rows]
        composed = [texts[s.slice_id] for s in kept] + [v["text"] for v in vision_rows]
        ordered = [embedding_text(contexts.get(i), c) for i, c in zip(ids, composed, strict=True)]

        # ⚠️ **在边界上归一化成纯 Python list。** 生产的嵌入器返回 np.ndarray，
        # 而下游的 `if vectors:` 对多元素 ndarray 抛 ValueError（见 Embedder 的 docstring）。
        # 归一化放在这里而不是每个用到的地方各判一次：类型只在这一行被关心，
        # 后面的代码从此与「嵌入器返回什么容器」无关。
        vectors = [[float(x) for x in row] for row in embedder.embed(ordered)]
        if len(vectors) != len(ids):
            raise ValueError(f"嵌入返回 {len(vectors)} 条，切片有 {len(ids)} 条")

        # ⚠️ **比的是 schema 的维度，不是嵌入器自称的维度。**
        # 第一版写的是 `len(vectors[0]) != embedder.dimensions` —— 那是拿嵌入器
        # 跟它自己比：一个声称 512 又返回 512 的嵌入器照样通过，然后向量进不了
        # `vector(1024)` 那一列。**一个永远不会失败的检查不是检查**（口径 F16）。
        # 被 `test_dimension_mismatch_raises` 抓出来的。
        #
        # `SELECTED_DIMENSIONS` 是这个数在代码里的唯一出处，SQL 的列定义也钉着它。
        if embedder.dimensions != SELECTED_DIMENSIONS:
            raise ValueError(
                f"嵌入器声称 {embedder.dimensions} 维，而 slice_embeddings.embedding "
                f"是 vector({SELECTED_DIMENSIONS})（retrieval.selection 是唯一出处）"
            )
        if len(vectors) > 0 and len(vectors[0]) != SELECTED_DIMENSIONS:
            raise ValueError(
                f"向量维度 {len(vectors[0])} ≠ vector({SELECTED_DIMENSIONS})"
            )

        store.insert_embeddings(
            token=token,
            rows=[
                {
                    "document_id": document_id,
                    "slice_id": sid,
                    "owner": owner,
                    "model": EMBED_MODEL_ID(),
                    # 与 `documents.index_ruleset` 是**同一个串**（`api/indexing.py` 唯一出处）。
                    "embed_ruleset": INDEX_RULESET,
                    # pgvector 的文本输入格式。传参形状由 verify_corpus_prod.py 第 1 步
                    # 对真库确认过——本地验不了（PGlite 没有 vector 扩展）。
                    "embedding": "[" + ",".join(repr(float(x)) for x in v) + "]",
                }
                for sid, v in zip(ids, vectors, strict=True)
            ],
        )

        # 词条（口径 F97）。标题 + 定位语 + 原文，**与查询侧走同一个分词函数**。
        titles = [s.title for s in kept] + [v["title"] for v in vision_rows]
        raw = [s.text for s in kept] + [v["text"] for v in vision_rows]
        store.insert_lexicon(
            token=token,
            rows=[
                {
                    "document_id": document_id,
                    "slice_id": sid,
                    "owner": owner,
                    "ruleset": INDEX_RULESET,
                    "context": contexts.get(sid),
                    "lexical_text": lexicon_text(t, contexts.get(sid), x),
                }
                for sid, t, x in zip(ids, titles, raw, strict=True)
            ],
        )

    return IngestResult(
        document_id=document_id,
        slice_count=len(kept) + len(vision_rows),
        embedded_count=len(kept) + len(vision_rows),
        skipped_pages=len(manifest.skipped_pages),
        restricted_pages_excluded=excluded,
        verdict=parsed.document.verdict,
        source_format=parsed.document.source_format,
        page_count=parsed.document.page_count,
        usable_page_count=parsed.document.usable_page_count,
        index_ruleset=INDEX_RULESET,
        vision_page_count=len(vision_rows),
        vision_skipped_over_cap=over_cap,
        contextualized_count=len(contexts),
        llm_cost_usd=llm_cost,
    )
