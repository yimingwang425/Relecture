"""F4 · 「这部分没看懂」：切片身份键、交集判定、注入的限定语。

    提问 → 检索得到本轮切片
         → 查本人 marks 中与本轮有交集的行（一次 `&&`，零模型调用）
         → 交集非空 → 在**本轮用户消息**前缀注入一句限定语（口径 F57）
         → 生成

## 这个模块存在的理由：**把三处会漂的东西各收成一处**

1. **切片身份键怎么拼**（`slice_key`）。写入侧（`POST /marks` 存进
   `confusion_marks.slice_ids`）与读取侧（每轮检索之后的 `&&`）拼法必须逐字节
   相同，否则交集**恒为空**——而恒为空的表现是「功能好像没生效」，
   没有任何东西会红。同 F3b 那条「跳过页 ∩ 命中页恒为空集」。

2. **限定语的原文**（`CONFUSION_NOTE`，口径 F58）。它出现在 prompt 里、
   出现在断言里、出现在验收脚本里；三处各写一份的话，改一次措辞会让
   断言继续绿着测一句已经不发出去的话。

3. **「要不要注入」这个判断**（`confusion_prefix`）。判断本身在数据库里做
   （`&&`），本函数只把「查回来的行」翻译成「加不加这段前缀」——
   一行代码，但它是**唯一**的那一行。

## ⚠️ 切片的身份是**两段**，不是一个 uuid（口径 F66）

`slices` 的主键是 `(document_id uuid, slice_id text)`，而 `slice_id` 是
**文档内局部 id**（`'s001'`）。2026-09-12 真库观测：**一门课的两份讲义都有
`s001`**。只拿 `slice_id` 当键的话，标记了 A 讲义的第一片会让 B 讲义的第一片
以后每次命中都注入——**误触发，且输出看起来完全正常**。

所以键是 `'<document_id>:<slice_id>'`。分隔符取 `:`：uuid 与我们的 slice_id
里都不可能出现它（uuid 只有十六进制与 `-`；slice_id 由 slicer 生成，形如
`s001`）。真拿到一个带 `:` 的 slice_id 时 `slice_key` 会**抛**，不会拼出一个
有歧义的键——歧义的键会在几个月后表现为「偶尔注入错了地方」。

## 替身检索没有 `document_id`，那时**一个键都不产**

`InMemoryRetriever`（阶段 3 的合成课件替身）不带 `document_id`。
编一个的话，那些键会与某一份真实讲义的键撞上；留空 `':whole'` 的话，
所有替身轮次共享同一个键，于是**任何一次替身标记都会污染其余全部替身轮次**。
两条都不要：没有 `document_id` 就不产键，`slice_keys_of()` 少给几个。
后果如实登记：**走替身的那条路（不传 `course_id`）标记不了**，
前端拿到空的 `slice_keys` 就不该显示那个按钮。那不是缺陷，
替身讲的是合成课件（口径 F02），标记它没有意义。

决策日期：2026-09-13
"""

from __future__ import annotations

from typing import Any

__all__ = ["CONFUSION_NOTE", "KEY_SEPARATOR", "slice_key", "slice_keys_of",
           "confusion_prefix", "matched_keys"]

#: 键的分隔符。见模块 docstring 里为什么是它。
KEY_SEPARATOR = ":"

#: 注入的限定语（口径 F58）。**一字不改**，改它要先改口径表。
#:
#: ⚠️ **刻意不写「讲得更详细」。** 那与 `paragraph_length` 的判定方向相反：
#: P2/P3 用户上它会推高重生成触发率（配置扫描实验实测 P3 触发 8/15），
#: 于是这一跳会顺带涨成本。措辞与详细程度轴必须正交——
#: 这不是文案偏好，是约束冲突决定的。
#:
#: ⚠️ **不注入上次的提问原文**（口径 F59）。上次问题与本次未必相关，
#: 而模型判断不了二者是否相关——要它区分等于要它报告一个它观测不到的量。
#: `confusion_marks.question` 存着，本轮一个字都不读。
CONFUSION_NOTE = "用户此前看过这部分内容并标记为没看懂。换一个切入点重讲，不要照搬上次的表述。"


def slice_key(document_id: str, slice_id: str) -> str:
    """`(document_id, slice_id)` → 一段字符串键。**唯一的拼法出处。**

    两段都不许为空、都不许含分隔符——拼出一个有歧义的键，
    表现是几个月后「偶尔注入错了地方」，而那时没有任何日志说得清为什么。
    """
    d, s = str(document_id), str(slice_id)
    if not d or not s:
        raise ValueError(f"切片身份两段都必须有值，得到 {d!r} / {s!r}")
    if KEY_SEPARATOR in d or KEY_SEPARATOR in s:
        raise ValueError(
            f"切片身份里出现了分隔符 {KEY_SEPARATOR!r}：{d!r} / {s!r}。"
            "拼出去的键会有歧义，而歧义的键表现为「偶尔注入错了地方」。"
        )
    return f"{d}{KEY_SEPARATOR}{s}"


def slice_keys_of(context: Any) -> list[str]:
    """本轮命中切片的身份键。**顺序即检索顺序，去重但不排序。**

    取 `context.meta['hit_documents']`（真检索后端提供，与 `slices` 逐项对齐），
    与 `api/api/events.py` 的 `_slice_identities()` 是**同一个来源**。

    ⚠️ **对不齐就一个都不产。** 长度不等意味着后端的两份输出不同步，
    而按序号硬配会把身份配错——配错的身份比没有身份更糟，它看起来是对的
    （同 `_slice_identities` 那条注释）。
    """
    meta = getattr(context, "meta", None) or {}
    hits = meta.get("hit_documents") or []
    slices = getattr(context, "slices", ()) or ()
    if len(hits) != len(slices):
        # 替身后端（没有 hit_documents）走的也是这条：0 != n。
        return []

    out: list[str] = []
    seen: set[str] = set()
    for h in hits:
        doc = h.get("document_id")
        sid = h.get("slice_id")
        if not doc or not sid:
            continue
        k = slice_key(doc, sid)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def matched_keys(rows: list[dict[str, Any]], round_keys: list[str]) -> list[str]:
    """交集里到底是哪几片。**只用于报告与验收，不参与是否注入的判断。**

    是否注入由 `confusion_prefix` 看 `rows` 空不空决定，而 `rows` 是数据库
    用 `&&` 算出来的。这里再算一次交集**不是第二处判据**——它算的是
    「哪几片」，不是「有没有」。两者若打架（rows 非空而这里空），
    说明数据库那一侧的 `&&` 与这里的键拼法不同源，
    调用方会把它原样放进响应里（见 `main.py`），不吞掉。
    """
    want = set(round_keys)
    hit: list[str] = []
    for r in rows:
        for k in r.get("slice_ids") or []:
            if k in want and k not in hit:
                hit.append(k)
    return hit


def confusion_prefix(rows: list[dict[str, Any]]) -> str:
    """查回来的 marks → 本轮用户消息的前缀。空则不注入。

    ⚠️ **这一行是「注入不是恒触发」那条守卫的被测对象。**
    一个恒触发的限定语，和一个永远不触发的规则一样糟，且更难发现——
    它的输出看起来完全正常，只是每一轮都在多讲一句。
    所以断言是成对的：有交集必须注入，**无交集必须不注入**。
    """
    return CONFUSION_NOTE if rows else ""
