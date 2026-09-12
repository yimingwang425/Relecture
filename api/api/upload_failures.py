"""上传失败的**分类**。取值域的唯一出处。

## 为什么要一个闭集，而不是把异常文本塞进事件

PRD §13.0 把 `deck_upload_failed` 登记为「本批不做」，触发条件写的是
**「接前端与账号之后」**，理由是「失败分类**依赖前端的上传流程形态**」。
阶段 6 做上传页，触发。

而「失败分类」这四个字是硬要求，不是修饰：**用户侧的处置每一类都不同**。

| 分类 | 用户该做什么 | 压成「上传失败」的后果 |
| --- | --- | --- |
| `unsupported_format` | 换一个 .pdf | 他会重试同一个文件 |
| `pptx_not_implemented` | **等我们做**，不是他的问题 | 他会以为 PPTX 永远不支持，去手动转 PDF |
| `empty_file` | 检查文件是不是没存盘 | — |
| `too_large` | 拆分或压缩 | 他会重试同一个文件 |
| `duplicate_in_course` | **什么都不用做**，已经在这门课里了 | 他会再传一次，再失败一次 |
| `unparsable` | 换一个能打开的文件；扫描件我们读不了 | 他会怀疑是网络问题 |
| `db_error` / `unknown` | **重试有意义** | 混进上面几类之后，「重试有没有用」这个问题答不出来 |

而在**分析**侧，合并的代价是另一个：§3.2 的「课件支持率」分母里，
`pptx_not_implemented`（我们没做）与 `unparsable`（这份文件读不了）
**不是同一件事**——前者会随交付消失，后者不会。
合成一个数之后，支持率的变化说不清是哪一边动了。

## 为什么是闭集 + 数据库护栏，而不是一个自由字符串

`payload` 是 jsonb，**写错一个字不会报错**（口径 F36 那笔交易的代价）。
一个拼错的 `unparseable`（多一个 e）会在分析时变成一个**新的类别**，
而它看起来完全正常——只是某一类的计数莫名其妙地少了。

所以取值域有两处护栏，都会红：
1. **这里**：`classify()` 只返回这个闭集里的值，`FAILURE_KINDS` 是 Python 侧的唯一出处；
2. **数据库**：`f3_event_enums_ok()` + 一条 CHECK（见
   `20260912180000_f3_upload_events.sql`）。不在闭集里的值 **23514 被拒**。

两份存在 ≠ 两份一致，所以 `tests/test_upload_events.py` 从 **SQL 原文**解析出
那个清单再逐项比对（口径 F33，与 `REQUIRED_KEYS` 那份镜像同一个做法）。

## ⚠️ 异常原文**不进事件**

`unparsable` 那一类的异常文本来自解析器，而解析器谈论的是**用户上传的文件内容**
（「第 12 页的对象流损坏」之类）。它进事件就等于把用户内容的碎片放进了分析数据，
而 F1 的对照臂刻意**不把描述原文写进事件**（`textMapped()` 压根不接受那个参数，
口径 F32 + PRD §12.2）——同一条纪律。

原文仍然有地方去：**它回给这一次请求的用户**（HTTP body，只有他自己看得到），
并留在服务端日志里。事件里只有分类。

决策日期：2026-09-12
"""

from __future__ import annotations

from typing import Any

__all__ = ["FAILURE_KINDS", "HTTP_STATUS_BY_KIND", "RETRYABLE_KINDS",
           "UploadFailure", "classify_http_status"]

#: **上传失败分类的闭集。Python 侧的唯一出处。**
#:
#: 顺序无意义（分析时按名字分组）。加一类要同时改
#: `20260912180000_f3_upload_events.sql` 里的 `f3_deck_upload_failure_kinds()`，
#: 否则新类会被数据库 23514 拒掉 —— **那是刻意的**：
#: 一个只在 Python 侧存在的新类别，在分析时没有人知道它该归到哪里。
FAILURE_KINDS: tuple[str, ...] = (
    "unsupported_format",      # 不是 .pdf / .pptx
    "pptx_not_implemented",    # .pptx：schema 已定，解析器未交付。**不是「不支持」**
    "empty_file",              # 0 字节
    "too_large",               # 超过 parse_service.app.MAX_UPLOAD_BYTES
    "duplicate_in_course",     # 这门课里已有同一份文件（唯一索引带 course_id）
    "unparsable",              # 解析器打不开 / 抛异常
    "db_error",                # PostgREST / 网络
    "unknown",                 # 兜底。**它出现就是一个待分类的 bug**，见下
)

#: 每一类对应的 HTTP 状态码。**一个类一个码，不复用**——
#: 复用会让前端按状态码分支时把两类并成一类，而它们的文案不同。
#:
#: `unknown` 给 500：它的含义是「我们没想到这种失败」，
#: 而 500 正是「服务端出问题了」。用 400 会把它说成用户的错。
HTTP_STATUS_BY_KIND: dict[str, int] = {
    "unsupported_format": 415,
    "pptx_not_implemented": 501,
    "empty_file": 400,
    "too_large": 413,
    "duplicate_in_course": 409,
    "unparsable": 422,
    "db_error": 502,
    "unknown": 500,
}

#: **重试有意义的那几类。** 前端按它决定要不要给「重试」按钮。
#:
#: 方向是刻意的：**把可重试的判成不可重试，用户少点一次按钮；
#: 反过来他会一直重试一个永远不会成功的上传**（同 `events.ts` 里
#: 瞬时/永久失败白名单的同一条判断）。所以这个集合取小。
RETRYABLE_KINDS: frozenset[str] = frozenset({"db_error", "unknown"})


class UploadFailure(Exception):
    """一次上传失败。`kind` 一定在 `FAILURE_KINDS` 里。

    `detail` 是**给这一次请求的用户看的**（进 HTTP body），
    **不进事件**——见模块 docstring 最后一节。
    """

    def __init__(self, kind: str, detail: str, *, extra: dict[str, Any] | None = None) -> None:
        if kind not in FAILURE_KINDS:
            # 自己先不许乱写。拼错一个字在 jsonb 里不会报错，
            # 而它会在分析时变成一个新类别（口径 F36 那笔交易的代价）。
            raise ValueError(f"未知的上传失败分类 {kind!r}；闭集是 {FAILURE_KINDS}")
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        #: 进事件的**结构化**补充（页数、字节数这类），不含自由文本。
        self.extra = extra or {}

    @property
    def status(self) -> int:
        return HTTP_STATUS_BY_KIND[self.kind]

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


def classify_http_status(status: int) -> str:
    """HTTP 状态码 → 分类。**只在没有 `UploadFailure` 时兜底用。**

    正常路径上分类由抛异常的那一处给出（它知道为什么失败）；
    这个函数是给「从状态码反推」的场合留的，**反推会丢信息**：
    比如 415 与 501 都是「格式不对」的家族，而状态码能分开它们
    正是因为上面那张表一个类一个码。
    """
    for kind, code in HTTP_STATUS_BY_KIND.items():
        if code == status:
            return kind
    return "unknown"
