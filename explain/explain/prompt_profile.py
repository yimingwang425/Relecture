"""`prompt_profile_id` —— F04 三个标识里的第三个。

## 分工（口径 F04）

===================== ================================================
`config_fingerprint`  A 类**阈值**变没变（`a_rules.StyleConfig.fingerprint`）
`metric_set_id`       **指标集**是不是同一个（`a_rules.StyleConfig.metric_set_id`）
`prompt_profile_id`   **prompt 侧**变没变，含 B 类三条轴（本模块）
===================== ================================================

前两个都不含 B 类字段，这是刻意的：B 类进指纹会让两个 A 类判定完全相同的用户
拿到不同指纹，`verify` 里「点内指纹唯一」那类守卫在真实用户数据上直接噪声化。
但 B 类**确实会改变模型的输出**，所以它需要自己的标识——否则「讲解质量变了，
是阈值变了还是 prompt 变了」这个问题一个标识答不出来。

## 为什么在 Python 侧，而且**不建 FastAPI**（口径 F39）

三个标识必须同处，否则就是「TS 一份 Python 一份必然漂」（F13 / 约束 3）的复发。
但关键事实是：**`prompt_profile_id` 只在分析时被消费，不在写入时。**所以

- 算法住在 Python 侧（与另两个标识同处，风格也照抄：canonical JSON → sha256 前 12 位）；
- **埋点事件里存的是构成它的原始字段**：B 类三条轴的取值 + prompt 模板版本
  （见 `web/lib/style/events.ts` 与迁移 `20260910140000_f1_style_events.sql`）；
- 分析时由本模块从原始字段算出 id。

TS 从头到尾不碰这个算法，也不需要每写一条事件就发一次网络请求。
FastAPI 推迟到 F2 接线时再建——那时它是真需要的；现在建等于为一个纯函数启用
整条 Railway 部署链、多一条 PRD §13.3 的出境记录、多一道接缝。

## 一处如实登记

任务书 §一 说「`explain/` 里 `metric_set_id` 与 `config_fingerprint` 的实现」。
**实际上那两个方法定义在 `实验脚本/A类规则计算脚本/a_rules/config.py`**，
`explain/log.py` 只是每轮把它们**记进日志**。本模块放在 `explain/` 而不是 `a_rules/`，
理由是后者刻意不含 B 类字段（F04），把 B 类塞进去会破坏那条口径；
`explain/` 是渲染 prompt（`style_prompt.py`）并记录三个标识（`log.py`）的地方，
是这第三个标识的自然归属。任务书 §六「Python 侧改动在 explain/」也指向这里。

决策日期：2026-09-10
"""

from __future__ import annotations

import hashlib
import json

__all__ = [
    "B_CLASS_AXES",
    "PROFILE_ALGO_VERSION",
    "prompt_profile_id",
    "profile_payload",
]

#: 进 `prompt_profile_id` 的三条轴。**顺序无关**（下面按 key 排序），
#: 但集合本身是口径的一部分：改这个集合就是改标识的语义，
#: 所以要连 `PROFILE_ALGO_VERSION` 一起改。
B_CLASS_AXES: tuple[str, ...] = ("use_examples", "structure", "depth")

#: **算法版本**，与被算的内容无关。
#:
#: 它和 `template_version` 不是一回事，两者都要有：
#:   · `template_version` 变 = prompt 的**措辞**变了（住在 f1_samples/style_axes.py）
#:   · `PROFILE_ALGO_VERSION` 变 = **算 id 的方式**变了（比如往里加了第四条轴）
#: 少了后者，改算法会让历史 id 与新 id 在同一个命名空间里静默相撞——
#: 两批数据的 id 不同，但没人能说出「不同是因为 prompt 变了还是算法变了」。
#: 同一个道理在 a_rules 那边是 `CONFIG_VERSION` 进 `metric_set_id`。
PROFILE_ALGO_VERSION = "1.0"


def profile_payload(
    b_class: dict[str, str], template_version: str, tone: str = "neutral"
) -> dict[str, object]:
    """把原始字段整理成待哈希的规范结构。**先验取值非空**，不静默补默认值。

    埋点事件里存的就是这几个字段，所以这个函数的入参形状 = 事件 payload 的形状：
    `style_onboarding_completed` / `style_text_mapped` / `style_config_changed`
    三个事件各带一份 `config`（含 B 类三条轴）与 `prompt_template_version`。

    缺轴时**抛异常**而不是回落默认值：补默认值会让「用户就是选了 more」和
    「这条数据缺 use_examples」算出同一个 id，而它们的处置完全不同（同 F32 的
    「回落必须显式」、flow.configFromPicks 的「缺轴抛异常」）。
    """
    if not template_version:
        raise ValueError("template_version 为空——它是 prompt 措辞版本，不能省")

    missing = [a for a in B_CLASS_AXES if not b_class.get(a)]
    if missing:
        raise ValueError(f"B 类轴缺取值：{', '.join(missing)}（不补默认值，见文档字符串）")

    from .prompt import SYSTEM_TEMPLATE_VERSION

    payload: dict[str, object] = {
        "algo_version": PROFILE_ALGO_VERSION,
        "template_version": template_version,
        # system 模板的版本（2026-09-19 起）。它与 `template_version` 不是一回事：
        # 后者是 B 类**措辞**的版本，前者是 system 骨架本身。改了 system 而 id 不变，
        # 等于两份不同的 prompt 共用一个标识——正是这个 id 要防的事。
        "system_version": SYSTEM_TEMPLATE_VERSION,
        "b_class": {a: b_class[a] for a in sorted(B_CLASS_AXES)},
    }
    # 语气（2026-09-19）：**只在它真的改变 prompt 时进 payload**。
    #
    # 这个 id 的语义是「prompt 侧变没变」，所以它应当是 **prompt 形状的指纹**。
    # `neutral` 渲染成空串（`style_prompt.TONE_FRAGMENTS`），prompt 逐字节不变——
    # 那么 id 也必须不变，否则历史数据会平白多出一批「prompt 变了」的假信号，
    # 而真相是什么都没变。反过来，非默认档确实多了一行，id 就必须不同。
    #
    # 这也是**没有抬 `PROFILE_ALGO_VERSION` 的理由**：算 id 的方式没变，
    # 变的是「prompt 里可能多一行」这件事本身，而它由 payload 里多出的键表达。
    if tone and tone != "neutral":
        payload["tone"] = tone
    return payload


def prompt_profile_id(
    b_class: dict[str, str], template_version: str, tone: str = "neutral"
) -> str:
    """prompt 侧标识，前 12 位十六进制。

    形状与 `StyleConfig.fingerprint()` / `.metric_set_id()` 完全一致——
    canonical JSON（`sort_keys=True`, `ensure_ascii=False`）→ sha256 → 前 12 位。
    照抄不是偷懒：三个标识会并排出现在同一条日志行与同一张报表里，
    长度或字符集不同会让「哪个是哪个」变成要去查文档的事。

    >>> a = prompt_profile_id({"use_examples": "more", "structure": "conclusion_first",
    ...                        "depth": "why"}, "b1")
    >>> b = prompt_profile_id({"depth": "why", "structure": "conclusion_first",
    ...                        "use_examples": "more"}, "b1")
    >>> a == b          # 字典顺序不影响
    True
    >>> len(a)
    12
    >>> a == prompt_profile_id({"use_examples": "more", "structure": "conclusion_first",
    ...                         "depth": "why"}, "b1", "neutral")   # 默认档不改变 id
    True
    >>> a == prompt_profile_id({"use_examples": "more", "structure": "conclusion_first",
    ...                         "depth": "why"}, "b1", "spoken")    # 非默认档必须不同
    False
    """
    canonical = json.dumps(
        profile_payload(b_class, template_version, tone), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
