"""system / messages 组装 + prompt caching 断点。

## 检索片放 user turn，不放 system（口径 X04）

第 0 周的架构是「课件全量进 system」，缓存断点打在 system 末尾就能把整份课件
缓存住。**接检索之后这个形态变了**：

- 课件全文不在上下文里
- 检索片**每轮不同**，天然不可缓存
- 会话内真正不变的只剩：风格约束 + 课件清单（system）、以及**对话历史**

所以断点位置不变（system 末尾 + 最近一轮消息末尾），但**收益结构完全变了**，
且带来一个新的失效形态——见下。

## ⚠️ system 段可能低于最小可缓存长度，且失败是静默的

接检索后 system 只剩风格片段 + 课件清单，约数百 token，
**低于模型的最小可缓存 prefix 长度时，缓存断点不生效且不报错**。

这是「会静默改变数字的问题」（通则六）。处置：

- `estimate_system_tokens()` 给出估算，进 metadata
- 低于 `MIN_CACHEABLE_TOKENS` 时报「**未生效**」，而不是「未命中」
  ——这两句话含义完全不同，混同会让报告里写着「缓存开了」而实际没开

**估算刻意用一个粗糙但确定的方法**（见函数注释）：它只用来判断量级，
真实 token 数由 API 返回的 `usage` 给出，日志里两个都记。

## 再往上一层：PRD §7.3 的论断在新架构下待验证

§7.3 写「课件内容会话内不变 → 必须启用 prompt caching」。
**这句话的前提是课件全文在 system 里。** 前提没了，论断悬空。
本轮不改 PRD，但报告必须回答「接检索后 caching 还剩多少收益」。

决策日期：2026-08-22
"""

from __future__ import annotations

from typing import Any

from .context import ContextPack

__all__ = [
    "MIN_CACHEABLE_TOKENS",
    "SYSTEM_TEMPLATE",
    "build_system",
    "build_user_turn",
    "estimate_system_tokens",
    "system_cache_status",
]

#: Sonnet 系最小可缓存 prefix 长度。低于它，`cache_control` 静默无效。
MIN_CACHEABLE_TOKENS = 1024

SYSTEM_TEMPLATE = """你是一位课件讲解助手，帮助学生理解他上传的课程材料。

## 讲解风格要求

{style_rules}

## 材料范围

{manifest}

## 你看到的是片段，不是全文

每轮我会给你若干与提问相关的**课件片段**。它们是检索出来的，**不保证包含
回答该问题所需的全部内容，也不会标出缺了什么**。

因此：

- 片段够回答，就基于片段讲，并指出内容在第几页。
- **片段不够回答，就说清楚缺什么**，不要用通用知识补全后当作课件内容讲。
- 确实需要用到课件之外的知识时，显式写明「以下内容超出课件范围」。
"""


def _render_manifest(context: ContextPack, course_label: str) -> str:
    """课件清单。**只写事实，不写兜底文案**（口径 X07）。

    `skipped_pages` 在这里的作用不是让模型道歉，而是让它**知道有些页读不出来**——
    否则它面对一个内容在图里的提问时，最可能的行为是基于标题编一段像样的讲解
    （PRD §6.2：「更高危险——它看起来成功了」）。

    兜底文案是 F3b 的事，本轮只保证这条信息在上下文里。
    """
    lines = [f"当前课程材料：{course_label}"]
    if context.unusable_files:
        lines.append(
            "以下文件整份无法读取（图片型课件）：" + "、".join(context.unusable_files)
        )
    if context.skipped_pages:
        by_kind: dict[str, list[int]] = {}
        for p in context.skipped_pages:
            by_kind.setdefault(p.kind, []).append(p.index)
        for kind, pages in sorted(by_kind.items()):
            shown = "、".join(str(n) for n in sorted(pages)[:30])
            more = "…" if len(pages) > 30 else ""
            lines.append(f"以下页无法提取文本（{kind}）：第 {shown}{more} 页")
        lines.append(
            "这些页的内容**存在但读不了**，不等于课件没讲。"
            "若提问指向这些页，如实说明它读不了，不要说「课件里没有提到」。"
        )
    else:
        lines.append("本课程材料没有无法读取的页。")
    return "\n".join(lines)


def build_system(style_rules: str, context: ContextPack, course_label: str) -> str:
    """system prompt。**会话内不变**——它不含任何逐轮变化的内容。

    检索片刻意不在这里：它每轮不同，放进 system 会让缓存每轮失效，
    等于把唯一还能缓存的东西也毁掉。
    """
    return SYSTEM_TEMPLATE.format(
        style_rules=style_rules, manifest=_render_manifest(context, course_label)
    )


def build_user_turn(context: ContextPack, question: str) -> str:
    """一轮用户消息 = 本轮检索片 + 提问。

    片段带页号，因为讲解要能溯源到页；带标题，因为脱离标题的 bullet
    往往不可讲解（slicer S01 的切片单位就是按这个定的）。
    """
    if context.is_empty:
        # 空检索是显式失败，比「答非所问」安全（PRD §6.2）。如实告诉模型。
        return f"【本轮没有检索到任何课件片段】\n\n学生的问题：{question}"

    parts = ["【课件片段】"]
    for s in context.slices:
        pages = "、".join(str(p) for p in s.page_indices)
        head = f"— {s.source_file} 第 {pages} 页"
        if s.title:
            head += f"｜{s.title}"
        parts.append(f"{head}\n{s.text}")
    parts.append(f"\n学生的问题：{question}")
    return "\n\n".join(parts)


def estimate_system_tokens(system: str) -> int:
    """粗估 token 数，**只用来判断量级**。

    刻意不引入分词器：那会给 `explain` 加一个第三方依赖，而这个数唯一的用途
    是回答「system 段够不够到最小可缓存长度」——一个数量级问题。
    真实值由 API 返回的 usage 给出，日志两个都记。

    口径：CJK 字符按 1 token，其余按 4 字符 1 token（英文的常见近似）。
    """
    cjk = sum(1 for ch in system if "　" <= ch <= "鿿" or "＀" <= ch <= "￯")
    rest = len(system) - cjk
    return cjk + rest // 4


def system_cache_status(system: str) -> dict[str, Any]:
    """system 段的缓存可行性。**「未生效」与「未命中」必须分开报。**"""
    est = estimate_system_tokens(system)
    cacheable = est >= MIN_CACHEABLE_TOKENS
    return {
        "system_chars": len(system),
        "system_tokens_estimated": est,
        "min_cacheable_tokens": MIN_CACHEABLE_TOKENS,
        "system_cache_expected_effective": cacheable,
        "note": (
            "system 段达到最小可缓存长度，断点应当生效"
            if cacheable
            else "system 段低于最小可缓存长度，缓存断点**不生效**（不是未命中）。"
            "接检索后 system 只剩风格约束与课件清单，这是预期形态，"
            "但报告里不得写成「缓存已开启」"
        ),
    }
