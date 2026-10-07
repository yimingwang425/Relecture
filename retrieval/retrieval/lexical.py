"""词法检索的分词与融合（2026-10-07，口径 F97）。**纯函数，零第三方依赖。**

## 为什么要词法这一路

向量检索对**精确词**弱：课件里的缩写、公式符号、专有名词（`U-235`、`Carnot`、
`T-account`）在嵌入空间里会被「意思相近」的东西稀释。词法这一路专门接住它们，
两路用 RRF 融合（`rrf_fuse`），谁也不替代谁。

## ⚠️ 本项目的特殊处：提问是中文，课件多是英文

`retrieval/README.md` §三 记过：评测集的中文问法与英文语料**两侧 token 零重合**。
所以拿提问原文去做词法检索，在主场景下**恒召回为空**——那是一条永不触发的通道
（通则七 #5）。词法这一路的查询词因此来自**查询改写**（`explain.rag.QueryRewriter`），
它产出课件语言的关键词（中文概念 → 英文术语、公式、缩写）。没有改写结果时
才退回提问原文，那时对中文课件仍然有效。

## 分词：不引入分词器

- 拉丁字母与数字：小写、按非字母数字切开、去少量英文停用词、做一层**轻量词尾归一**
  （`engines` / `engine`）。不引 Porter / Snowball：那是一个第三方依赖，
  而这里只需要「同一个词的单复数与时态落在同一个词条上」。
- 中日韩字符：**二元组**（`热机效率` → `热机 机效 效率`），单字成串时保留单字。
  这是不依赖词典的 CJK 检索的常规做法；代价是会产生一些无意义的二元组，
  它们在排序里的权重被 `ts_rank_cd` 的覆盖度稀释。

**索引侧与查询侧必须走同一个函数**（`tokens`）——两侧各写一份的话，
哪天一侧改了归一规则，召回会静静地掉到零，而那看起来和「这门课没讲」一模一样。
入库的是空格拼起来的词条（`lexical_text`），数据库用 `to_tsvector('simple', …)`
原样建索引，不再做任何语言处理。

## 融合：RRF，k = 60

Reciprocal Rank Fusion（Cormack, Clarke & Büttcher, SIGIR 2009）：
`score(d) = Σ 1 / (k + rank_i(d))`。只用名次、不用分数，所以余弦相似度与
`ts_rank_cd` 这两个量纲完全不同的分数不需要归一化。**k = 60 取自原论文**，
不是在本项目数据上调出来的——本项目没有足够的评测题去调它（R17）。

决策日期：2026-10-07
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

__all__ = [
    "LEXICAL_RULESET_VERSION",
    "RRF_K",
    "tokens",
    "lexical_text",
    "tsquery",
    "rrf_fuse",
]

#: 分词口径版本。`tokens()` 的任何改动都必须递增——它进 `slice_lexicon.ruleset`
#: 的拼法（见 `explain.rag.INDEX_RULESET`），旧词条与新查询不会被静默混用。
LEXICAL_RULESET_VERSION = "lex-1"

#: RRF 的平滑常数。**取自原论文，不是本项目调出来的**（见模块说明）。
RRF_K = 60

#: 查询侧最多取多少个词条。挡的是「一段很长的历史被改写成几十个关键词」，
#: 那会让 OR 查询几乎匹配所有片。**是净空不是口径**。
MAX_QUERY_TERMS = 48

_LATIN = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]+")

#: 少量英文停用词。只收「出现在几乎每一片里、对排序毫无信息」的那一批。
_STOP = frozenset(
    "a an and are as at be been but by for from has have in is it its of on or that the "
    "this to was were will with which what how why when where who does do can not no".split()
)


def _stem(w: str) -> str:
    """轻量词尾归一。**只追求「单复数与常见时态落到同一个词条」**，不追求语言学正确。"""
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("sses"):
        return w[:-2]
    if w.endswith("ing") and len(w) > 5:
        return w[:-3]
    if w.endswith("ed") and len(w) > 4:
        return w[:-2]
    if w.endswith("es") and len(w) > 4 and w[-3] in "sxz":
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str) -> list[str]:
    """文本 → 词条列表（保序、可重复）。**索引侧与查询侧共用这一个函数。**"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    out: list[str] = []
    # 先把 CJK 段抠出来做二元组，剩下的按拉丁规则切。
    pos = 0
    for m in _CJK.finditer(t):
        out.extend(_latin(t[pos:m.start()]))
        run = m.group(0)
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
        pos = m.end()
    out.extend(_latin(t[pos:]))
    return out


def _latin(segment: str) -> Iterable[str]:
    for w in _LATIN.findall(segment):
        if w in _STOP:
            continue
        yield _stem(w)


def lexical_text(*parts: str | None) -> str:
    """入库用：把若干段文本的词条空格拼起来。数据库对它做 `to_tsvector('simple', …)`。"""
    return " ".join(tok for p in parts if p for tok in tokens(p))


def tsquery(*parts: str | None) -> str:
    """查询用：去重后用 `|`（OR）连起来，交给 `to_tsquery('simple', …)`。

    词条只含 `[a-z0-9]` 与 CJK 字符（见 `tokens`），所以单引号包起来即安全，
    不存在 tsquery 语法注入。没有词条时返回空串——调用方据此**跳过**这一路，
    而不是发一个空查询（`to_tsquery('simple', '')` 会报 NOTICE 并匹配不到任何东西）。
    """
    seen: dict[str, None] = {}
    for p in parts:
        if not p:
            continue
        for tok in tokens(p):
            seen.setdefault(tok, None)
            if len(seen) >= MAX_QUERY_TERMS:
                break
    return " | ".join(f"'{t}'" for t in seen)


def rrf_fuse(*rankings: Sequence[tuple[str, str]], k: int = RRF_K) -> list[tuple[tuple[str, str], float]]:
    """多路排名 → 融合排名。每路是按名次排好的 `(document_id, slice_id)` 序列。

    并列时按**首次出现的路序与名次**定先后（向量那一路排在前面传入），
    保证同一组输入两次融合结果逐项相同（R09 的同一条：不确定的排序让报告不可复现）。
    """
    score: dict[tuple[str, str], float] = {}
    first_seen: dict[tuple[str, str], tuple[int, int]] = {}
    for li, ranking in enumerate(rankings):
        for rank, key in enumerate(ranking, start=1):
            score[key] = score.get(key, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(key, (li, rank))
    return sorted(score.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
