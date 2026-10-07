"""检索索引的口径（2026-10-07，口径 F97 / F99）。**摄入与检索共用这一处。**

一份文档的检索索引由三样东西决定：组文本口径（`retrieval.compose`）、
定位语 prompt 版本（`explain.rag`）、分词口径（`retrieval.lexical`）。
三者拼成 `INDEX_RULESET`，写进 `documents.index_ruleset`、`slice_embeddings.embed_ruleset`、
`slice_lexicon.ruleset` 三处——**同一个字符串，只在这里拼一次**。

摄入写、检索读，两边各拼一份的话，哪天一边多了一段，检索按新串去找、
库里存的是旧串，于是什么都召不回——而那看起来和「这门课没讲」一模一样
（`retriever.py` 里 `EMBED_MODEL_ID` 那条注释的同一个坑）。

`LEGACY_RULESET` 是 2026-10-07 之前的唯一口径，也是迁移
`20261007100000` 给 `documents.index_ruleset` 的默认值。两者必须相等，
`tests/test_indexing.py` 从迁移原文里把默认值解析出来比对。

决策日期：2026-10-07
"""

from __future__ import annotations

__all__ = ["INDEX_RULESET", "LEGACY_RULESET", "embedding_text", "lexicon_text"]


def _index_ruleset() -> str:
    from retrieval.compose import EMBED_TEXT_RULESET_VERSION
    from retrieval.lexical import LEXICAL_RULESET_VERSION

    from explain.rag import CONTEXT_PROMPT_VERSION

    return f"{EMBED_TEXT_RULESET_VERSION}+{CONTEXT_PROMPT_VERSION}+{LEXICAL_RULESET_VERSION}"


def _legacy_ruleset() -> str:
    from retrieval.compose import EMBED_TEXT_RULESET_VERSION

    return EMBED_TEXT_RULESET_VERSION


INDEX_RULESET = _index_ruleset()
LEGACY_RULESET = _legacy_ruleset()


def embedding_text(context: str | None, composed: str) -> str:
    """送去嵌入的文本 = 定位语 + 组文本（T0）。**定位语缺失时就是组文本本身。**

    定位语放在前面：它说的是「这一片在整份课件里是什么」，是读这一片的上下文，
    与 Anthropic 的 Contextual Retrieval 做法一致（定位语在前、原文在后）。
    """
    return f"{context}\n\n{composed}" if context else composed


def lexicon_text(title: str | None, context: str | None, text: str) -> str:
    """入库的词条。标题与定位语一起进——定位语里的中文括注
    （「Carnot efficiency（卡诺效率）」）是中文提问在英文课件上唯一能字面命中的东西。"""
    from retrieval.lexical import lexical_text

    return lexical_text(title, context, text)
