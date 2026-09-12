"""阶段 5 · **检索从替身换成真库。**

    提问 → OpenAI 嵌入 → match_slices(course_id) → 取切片原文 → ContextPack

## 为什么住在 `api/` 而不是 `explain/`

`explain` 对着 `context.Retriever` 协议编程，**核心不认识任何后端**：
`InMemoryRetriever` 不需要网络，`RetrievalRetriever` 需要 numpy/openai，
而本类还需要 Supabase 的连接与**用户自己的 JWT**。

JWT 是分界线。`explain` 是一个无状态的生成库，它不该知道「谁在问」——
一旦它拿着 token，RLS 的边界就从「数据库执行」漂进了生成库里。
所以本类住在服务端，`explain` 那一侧一行都不用改（协议已定死）。

## 每次请求新建一个，**不缓存**

`RetrievalRetriever` 的 docstring 写着「构造代价高（解析 + 切片 + 嵌入），
故由调用方构造一次复用」——**那条对本类不成立，且反过来**：
本类的构造代价是零（存几个字段），而它持有的 token 属于**某一个用户**。
提到模块作用域或 `lru_cache` 上去，就是让一个用户的检索带着另一个用户的凭据跑。
`api/main.py` 的 `_default_retriever()` 那个 `@lru_cache` 是给替身用的，
替身不持有任何人的东西。

## 扩展模式：**只实现 `none`，而且不实现的那两种要吵**

`retrieval.selection.SELECTED_MODE` 是那个选择的唯一出处，当前是 `"none"`
（实测扩展无收益，R17）。`match_slices` 里没有任何邻接扩展。
所以本类在构造时**断言 `SELECTED_MODE == "none"`**：
选型哪天改成 `neighbor`，这里会当场抛，而不是**静默地继续按 none 跑**——
那种失败的表现是「换了扩展模式但召回没变」，最容易被读成「扩展模式没用」
（同 `retrieval/embed.py` 缓存键那条注释说的坑）。

## `source_file` 为什么可能带一截 id

`ContextSlice` 的身份是 `(source_file, slice_id)`，而 `slice_id` 是**文档内局部 id**
（`'s003'`，见 `20260911100100_f2_embeddings.sql`）。用 `filename` 当 `source_file`
的话，**同一门课里两份同名文件**会让两个不同的切片撞成同一个身份——
`ContextPack.signature()` 从此分不开它们，而签名的全部用途就是核验
「两组拿到的是不是同一份上下文」。

同名是允许的：唯一索引是 `(owner, course_id, content_sha256)`，
**同名不同内容可以共存**（改了一版重新上传，文件名往往不变）。
所以同名时在标签后缀上一截 `document_id`。不同名时不加——
那一截会原样进 prompt 给学生看（`build_user_turn` 拿它当出处标签）。

决策日期：2026-09-12
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SupabaseRetriever", "EMBED_MODEL_ID", "label_documents"]


def EMBED_MODEL_ID() -> str:  # noqa: N802 —— 它是个取值，不是类
    """写进 / 查出 `slice_embeddings.model` 的字符串。

    **与 `api.ingest.EMBED_MODEL_ID` 必须是同一个值**，否则摄入写进去的向量
    检索时按 `model` 过滤根本匹配不上——表现是「什么都召不回」，
    而那看起来和「这门课没有内容」一模一样。
    所以这里直接复用它，不另拼一份。
    """
    from .ingest import EMBED_MODEL_ID as _ingest_model_id

    return _ingest_model_id()


#: 同名时后缀至少取这么长。纯粹是可读性下限，不是唯一性保证——
#: 唯一性由 `_shortest_unique_prefix` 逐位加长直到真的分得开。
_MIN_SUFFIX = 4


def _shortest_unique_prefix(ids: list[str]) -> int:
    """能把这一组 id 分开的**最短**前缀长度；分不开就返回最长的那个长度。

    ⚠️ **不要写死 8 位。** 第一版写的是 `str(id)[:8]`，
    被 `test_same_filename_gets_disambiguated` 抓出来：
    测试数据里两个 uuid 的前 8 位相同，两个标签于是仍然相撞——
    **而撞了不会报错**，只会让 `ContextPack.signature()` 从此分不开那两片。

    「uuid 前 8 位通常不重复」是个概率陈述，而这里要的是保证。
    逐位加长到真的分得开，代价是标签偶尔长一点。
    """
    uniq = set(ids)
    longest = max((len(i) for i in ids), default=0)
    for n in range(_MIN_SUFFIX, longest + 1):
        if len({i[:n] for i in ids}) == len(uniq):
            return n
    return longest


def label_documents(documents: list[dict[str, Any]]) -> dict[str, str]:
    """`document_id → 展示用标签`。同名的才加 id 后缀（见模块 docstring）。"""
    by_name: dict[str, list[str]] = {}
    for d in documents:
        by_name.setdefault(d["filename"], []).append(str(d["id"]))

    out: dict[str, str] = {}
    for name, ids in by_name.items():
        if len(ids) == 1:
            out[ids[0]] = name
            continue
        n = _shortest_unique_prefix(ids)
        for i in ids:
            out[i] = f"{name}#{i[:n]}"
    return out


@dataclass
class SupabaseRetriever:
    """`context.Retriever` 的真实后端。**协议只要求一个 `fetch()`。**

    `store` 是 `SupabaseClient`，`embedder` 是 `retrieval.embed.OpenAIEmbedder`
    （或断言里注入的确定性替身）。所有数据库请求带 `token`，RLS 由数据库执行——
    与 `/me/style`、与摄入同一条路径，服务端不持有 service_role key（口径 F40）。
    """

    store: Any
    embedder: Any
    token: str
    course_id: str
    #: 预算 B=5。与评测集报告的 `@5` 口径一致——换预算等于换口径
    #: （同 `retrieval_adapter.RetrievalRetriever` 的那条注释）。
    budget: int = 5
    #: 这门课的文档清单。不传则 `fetch()` 时现取。
    #: 传进来是为了让调用方能在一次请求里复用（`/explain` 要用课程名做 course_label）。
    documents: list[dict[str, Any]] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        from retrieval.selection import SELECTED_MODE

        if SELECTED_MODE != "none":
            # **吵着失败，不静默按 none 跑。** 见模块 docstring。
            raise RuntimeError(
                f"retrieval.selection.SELECTED_MODE 现在是 {SELECTED_MODE!r}，"
                "而 match_slices 里没有任何邻接扩展 —— 本后端只实现了 'none'。"
                "静默继续的话，表现是「换了扩展模式但召回没变」，"
                "而那最容易被读成「扩展模式没用」。"
            )

    # ---------------------------------------------------------------- 清单

    def _documents(self) -> list[dict[str, Any]]:
        if self.documents is None:
            self.documents = self.store.documents_in_course(self.token, self.course_id)
        return self.documents

    @staticmethod
    def _skipped_and_unusable(documents: list[dict[str, Any]], labels: dict[str, str]):
        """把 `documents.unusable_pages` 还原成 `SkippedPageRef`（口径 X07）。

        ⚠️ **一处如实登记的缺口。** `slicer` 的 `manifest.skipped_pages` 有三个字段
        （`index` / `kind` / `reason`），而**落库的只有 `documents.unusable_pages`**，
        它是 parse 侧的 `UnusablePage`，只有 `index` 与 `kind`。两个后果：

          · `reason` 这里由 `slicer.core.SKIP_REASONS` 按 `kind` 反查。
            那是这些文案的**唯一出处**（slicer 当时也是从它取的），所以不是抄第二份。
          · slicer 的第二个分支——「可用页但无文本块（异常，应回查解析）」——
            **不在 `unusable_pages` 里，因而到不了这里**。它在 slicer 自己的注释里
            写着「理论上不该出现」，但「不该出现」不等于「不会出现」。
            **触发条件**：`ingest` 返回的 `skipped_pages` 计数与
            `documents.unusable_pages` 的长度对不上时——那说明这个分支真的发生了。
            届时要给 `documents` 加一列存 slicer 那张表，那是一次迁移。

        `unusable_files` 取 `verdict='unusable'` 的整份文件（如扫描件）。
        """
        from slicer.core import SKIP_REASONS

        from explain.context import SkippedPageRef

        skipped: list[SkippedPageRef] = []
        unusable: list[str] = []
        for d in sorted(documents, key=lambda x: labels[x["id"]]):
            if d.get("verdict") == "unusable":
                unusable.append(labels[d["id"]])
            for p in d.get("unusable_pages") or []:
                kind = str(p.get("kind", "unknown"))
                skipped.append(SkippedPageRef(
                    source_file=labels[d["id"]],
                    index=int(p["index"]),
                    kind=kind,
                    reason=SKIP_REASONS.get(kind, "不可用页"),
                ))
        return tuple(skipped), tuple(unusable)

    # ---------------------------------------------------------------- 协议

    def fetch(self, question: str):
        """协议要求的唯一方法。检索 query = **本轮提问原文**（口径 X01）。"""
        from retrieval.compose import EMBED_TEXT_RULESET_VERSION
        from retrieval.selection import (
            SELECTED_DIMENSIONS,
            SELECTED_MODE,
            SELECTED_MODEL,
            SELECTED_VARIANT,
        )

        from explain.context import ContextPack, ContextSlice

        t0 = time.monotonic()
        documents = self._documents()
        labels = label_documents(documents)
        skipped, unusable = self._skipped_and_unusable(documents, labels)

        # ---- 嵌入。**这是 PRD §13.3 的第一次出境在检索侧的那一半**：
        #      出去的是提问原文，不是课件（课件在摄入时已经出去过一次）。
        vector = [float(x) for x in self.embedder.embed([question])[0]]
        if len(vector) != SELECTED_DIMENSIONS:
            # 与摄入那一侧同一条判据：断**实际返回**的长度，不是嵌入器自称的
            # （口径 F16 —— 拿嵌入器跟它自己比永远不会失败）。
            # 维度不符时 `match_slices` 会报一个 pgvector 的类型错误，
            # 那句话读起来像「数据库坏了」，而其实是选型与 schema 不一致。
            raise ValueError(
                f"嵌入实际返回 {len(vector)} 维，而 match_slices 的 p_query 是 "
                f"vector({SELECTED_DIMENSIONS})（retrieval.selection 是唯一出处）"
            )
        embed_ms = (time.monotonic() - t0) * 1000

        # ---- 检索
        t1 = time.monotonic()
        rows = self.store.match_slices(
            token=self.token, course_id=self.course_id, query=vector,
            model=EMBED_MODEL_ID(), ruleset=EMBED_TEXT_RULESET_VERSION,
            limit=self.budget,
        )
        match_ms = (time.monotonic() - t1) * 1000

        # ---- 补原文。键是 `(document_id, slice_id)` **两段**，不是 slice_id 一段：
        #      slice_id 是文档内局部 id，一门课两份讲义都有 's001'。
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        if rows:
            for s in self.store.slices_in_documents(
                    self.token, [r["document_id"] for r in rows]):
                by_key[(s["document_id"], s["slice_id"])] = s

        slices: list[ContextSlice] = []
        #: 与 `slices` **逐项对齐**的 `(document_id, slice_id)`。
        #:
        #: ⚠️ **为什么必须有这个，而不能用 `source_file` 代替。**
        #: `source_file` 是**文件名**，而同一份文件可以挂在不同课程下
        #: （唯一索引是 `(owner, course_id, content_sha256)`，口径 F47 明确要支持
        #: 「同一份导论讲义用于两个学期」）。真库上就有这个形态：
        #: `MIT11_201F07_slides2.pdf` 同时在课程 A 和课程 B 里。
        #:
        #: 于是「召回的切片来自课程 A 的文件」这条判据**在真的召回了课程 B 时
        #: 同样成立**——两边文件名一模一样。那不是判据（同 F48：判据在它要区分的
        #: 两种情况下取值相同，就不是判据）。`document_id` 是唯一能分开的东西。
        #:
        #: 2026-09-12 记：上一版的验收判据就是按文件名写的，这个洞是真的。
        hit_documents: list[dict[str, str]] = []
        missing: list[str] = []
        for r in rows:
            key = (r["document_id"], r["slice_id"])
            src = by_key.get(key)
            if src is None:
                # 向量在、切片不在。**不静默跳过**——它意味着两张表不一致
                # （外键本应挡住），记进 meta 让它可被看见。
                missing.append(f"{key[0][:8]}#{key[1]}")
                continue
            slices.append(ContextSlice(
                source_file=labels.get(r["document_id"], r["document_id"][:8]),
                slice_id=r["slice_id"],
                page_indices=tuple(src.get("page_indices") or ()),
                title=src.get("title"),
                text=src.get("text") or "",
                char_count=int(src.get("char_count") or 0),
                score=float(r["similarity"]),
                via="hit",
            ))
            hit_documents.append({"document_id": r["document_id"],
                                  "slice_id": r["slice_id"]})

        return ContextPack(
            question=question,
            slices=tuple(slices),
            skipped_pages=skipped,
            unusable_files=unusable,
            meta={
                "backend": "supabase",
                "course_id": self.course_id,
                "budget": self.budget,
                "mode": SELECTED_MODE,
                # §13.0 的 `retrieval_executed` 要这几样，见 PRD。
                "embed_model": SELECTED_MODEL,
                "embed_dimensions": SELECTED_DIMENSIONS,
                "embed_variant": SELECTED_VARIANT,
                "embed_text_ruleset": EMBED_TEXT_RULESET_VERSION,
                "document_count": len(documents),
                # 与 `slices` 逐项对齐。验收断的是这个，不是 source_file —— 见上面
                # `hit_documents` 那段：同一份文件可以挂在两门课下，文件名分不开。
                "hit_documents": hit_documents,
                # 这门课有哪些文档。让「召回的 document_id 属于这门课」这条判据
                # 的**两端都来自服务端的同一次请求**之外还有一端可比：
                # 脚本手里有 ingest 时记下的 id（另一个来源），两边都能对。
                "course_document_ids": [str(d["id"]) for d in documents],
                "embed_latency_ms": round(embed_ms, 1),
                "match_latency_ms": round(match_ms, 1),
                "retrieval_latency_ms": round((time.monotonic() - t0) * 1000, 1),
                # 空列表是常态；非空表示 slice_embeddings 有行而 slices 没有，
                # 那是外键本应挡住的状态。留在这里，不吞掉。
                "slices_missing_text": missing,
            },
        )
