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
    "SYSTEM_TEMPLATE_VERSION",
    "build_system",
    "build_user_turn",
    "render_gap_note",
    "estimate_system_tokens",
    "system_cache_status",
]

#: Sonnet 系最小可缓存 prefix 长度。低于它，`cache_control` 静默无效。
MIN_CACHEABLE_TOKENS = 1024

#: system 模板的版本号。**措辞一改就要抬**，并且要抬进 `prompt_profile_id`
#: （`prompt_profile.py`）——否则改了 system 之后，新旧两批讲解在分析里共用一个标识，
#: 「质量变了是因为 prompt 变了吗」这个问题答不出来。
#:
#: s1 → s2（2026-09-19）：加了「怎么说话」那一节（去 AI 味）；
#: 并把「片段不够就说清楚」收紧为**对所问的那个问题**而言——
#: 此前 16 个样例里 14 个以「课件里还没讲到 X」收尾（人工核对清单记了两轮的伪影），
#: 而那些问题其实都被片段回答了。
#:
#: ⚠️ 写这一节时踩到过一次**两节打架**：初稿有「一段能说清的不要分成三段」，
#: 它直接抬高平均段长，而 P3 这一档量的就是段长（15–40 字），于是 P3 连续 10 次
#: 不达标（实测记录 E10 也说 P3 达标 72% 来自分段）。已删掉那一句：
#: **段落粒度归「讲解风格要求」管，这一节不许插手**。
#:
#: s3 → s4（2026-09-20）：**风格要求里多了「整篇长度」那一行**
#: （`style_prompt.render_total_length_rule`）。在那之前 prompt 只管「每段多长」，
#: 不管「整篇多长」——于是「详细程度」这条轴实际上只管段落粒度，
#: 而用户以为它管长度。理由与实测见那个函数的 docstring。
#:
#: ⚠️ **那一行不在本模板里，版本号却要跟着动。** 这个版本号的用途是让
#: 「prompt 变了而样例没重生成」这件事不再静默（`TestSamplesAreNotStale`
#: 与 pairs.json 里的 `system_template_version` 比对它）。而渲染出来的 system
#: 变没变，取决于模板**和**塞进去的风格片段两者——只盯模板会漏掉这一整类改动。
#:
#: s2 → s3（2026-09-19）：再收两条——不许描述片段本身（「片段里给了两个例子」），
#: 缺什么要说在开头且只一句。实测 s2 的样例里仍有开头元评论与结尾免责。
SYSTEM_TEMPLATE_VERSION: str = "s4"

SYSTEM_TEMPLATE = """你是一位课件讲解助手，帮助学生理解他上传的课程材料。

## 怎么说话

这一节对所有风格都生效，**风格要求不能覆盖它**。

- 直接讲内容。不要复述我的问题，不要用「好的」「让我们」「接下来我将」开场。
- 讲完就停。不写「总的来说」「综上所述」「希望这对你有帮助」这类收尾段。
- 不用「首先 / 其次 / 最后」当骨架，除非那真的是有先后的步骤。
- 不写「值得注意的是」「需要指出的是」「事实上」这类填充词，它们不承载信息。
- 不评价问题（「这是个好问题」），也不评价自己讲得怎么样。
- 不为了显得完整而重复已经说过的话。
  （**段落长短由下面的「讲解风格要求」决定，这一节不管它**——两节都管同一件事，
  就会出现一种看不见的冲突：风格要求说「段落要短」，这里说「别拆成几段」。）
- 不写「举例直观理解：」「所以本质是：」这类引导语，直接把例子或结论讲出来。
- **只回答被问的那个问题**，不要顺手扩展到没问的内容。
- **不要描述片段本身**（「片段里给了两个例子」「这一页只提到……」）。直接讲内容。

## 讲解风格要求

{style_rules}

## 材料范围

{manifest}

## 你看到的是片段，不是全文

每轮我会给你若干与提问相关的**课件片段**。它们是检索出来的，**不保证包含
回答该问题所需的全部内容，也不会标出缺了什么**。

因此：

- 片段够回答，就基于片段讲，并指出内容在第几页。**答完就停**——
  不要在结尾补一段「课件里还没讲到 X」。片段够回答所问的问题，就没有缺什么可说。
- **片段不够回答所问的问题，就说清楚缺什么**，不要用通用知识补全后当作课件内容讲。
  判断「够不够」的对象是**这一个问题**，不是这个主题的全部内容。
  要说的话**放在开头、一句话说完**，不要堆在结尾当免责声明。
- 确实需要用到课件之外的知识时，显式写明「以下内容超出课件范围」。

## 关于「片段完整性」提示

某些轮次我会在提问前附一条**片段完整性提示**，它由一次独立判断给出。

**把它当作输入，不是当作模板。** 用你自己的话向学生说清边界，
不要照抄提示里的措辞，也不要在讲解开头贴一段固定的免责声明。
学生要的是讲解，边界说明是讲解的一部分，不是包装。
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


def render_gap_note(gap: Any) -> str:
    """把兜底判断渲染成一条提示。**标注形态，不做门**（F3b 判断一）。

    三种情况里只有「完全没提」该阻断，而它在评测集里是 5/30。
    为少数情况给所有轮次加分支不划算，且模板文案违反 §10
    「兜底文案必须具体，不写『请稍后重试』这类无信息量的提示」。

    所以这里只给**事实**，措辞交给讲解 agent。
    """
    verdict = getattr(gap, "verdict", None)
    if verdict is None or verdict == "covered":
        # 无效样本（判断输出不合法）也走这条：宁可不提示，不要提示一个错的
        return ""

    lines = ["【片段完整性提示】"]
    if verdict == "insufficient":
        lines.append("本轮取到的片段里**没有**回答这个问题所需的内容。")
        lines.append(
            "注意措辞：能说的是「我取到的片段里没有」，"
            "**不能说「课件里没有」**——你看到的只是检索出来的若干片，不是全文。"
        )
    else:  # partial
        lines.append("本轮片段**提到了这个主题，但没有回答学生问的那一层**。")
        lines.append("讲清楚片段讲到了哪一步、缺的是哪一步。")

    # 规则通道（G03/G03a）：非 covered 且该课件有跳过页时出现
    hint = getattr(gap, "skipped_hint", ()) or ()
    if hint:
        pages = "、".join(str(p.index) for p in hint)
        lines.append(
            f"另外：本课件第 {pages} 页的内容全在图里，解析读不出来。"
            "**如果学生问的正是那部分，如实说明是「我读不了图」，"
            "而不是「课件里没有提到」**——课件提到了，只是我们读不了。"
        )
    return "\n".join(lines)


def build_user_turn(context: ContextPack, question: str, gap: Any = None,
                    prefix: str = "") -> str:
    """一轮用户消息 = `prefix` + 本轮检索片 + 提问。

    片段带页号，因为讲解要能溯源到页；带标题，因为脱离标题的 bullet
    往往不可讲解（slicer S01 的切片单位就是按这个定的）。

    ## `prefix` 是什么，为什么在这里而不在 system（口径 F57）

    F4 的「这部分没看懂」：本轮命中的切片与该用户历史标记有交集时，
    调用方（`api/api/main.py`）在这里塞一句限定语。

    **它必须在本轮消息里，不能进 system。** system 段是按「会话内不变」
    设计的（见上面 `build_system` 的 docstring），而 marks 逐轮变化——
    放进去会让一个按「不变」设计的位置每轮都不同。
    当前 system 约 406 token、缓存断点本来就未生效，所以这不是性能考量。

    ⚠️ **本模块不知道那句话说什么**，也不该知道：措辞是 F4 的口径
    （`api/api/marks.py` 的 `CONFUSION_NOTE`，口径 F58），
    在这里放一份副本就是同一句话的第二处出处。
    """
    note = render_gap_note(gap)
    if context.is_empty:
        # 空检索是显式失败，比「答非所问」安全（PRD §6.2）。如实告诉模型。
        head = "【本轮没有检索到任何课件片段】"
        return "\n\n".join(
            x for x in (prefix, head, note, f"学生的问题：{question}") if x)

    parts = [x for x in (prefix,) if x]
    parts.append("【课件片段】")
    for s in context.slices:
        pages = "、".join(str(p) for p in s.page_indices)
        h = f"— {s.source_file} 第 {pages} 页"
        if s.title:
            h += f"｜{s.title}"
        parts.append(f"{h}\n{s.text}")
    if note:
        parts.append(note)
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
