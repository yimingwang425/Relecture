"""风格配置 → 讲解 prompt 片段。**这是 F1 的接口形状。**

PRD §5.1 要求「配置以结构化参数存储，非自然语言描述（可程序化检查的前提）」。
F1 产出 `StyleConfig`，本模块把它渲染成模型能读的约束，审查侧则用同一份
`StyleConfig` 去判定——**同一个对象驱动两侧**，这就是「参数化注入」的含义。

## 口径 X05 · 配置同时驱动 prompt 与判定，两侧同步

配置扫描实验里这条必须写死，否则结论会被读错：

- 若**只收紧判定、不改 prompt**，触发率上升是**平凡结论**（判据变严了当然更容易违规）
- 两侧同步变化，测的才是真问题：**模型能不能遵守一个偏离常态的指令**

## 只渲染激活的维度

未激活的维度不进 prompt。理由不只是省 token——**要求模型做一件不计分的事，
既花钱又会污染归因**。v1 的术语维度已下线（PRD §3.2），
所以 prompt 里也不再要求术语标注；第 0 周那份 system prompt 里的
「专业术语首次出现时用『中文译名（English original）』标注」这一句，
在 v1 下必须删掉，否则模型会花输出 token 做一件审查根本不看的事。

## 阈值不直接报给模型

`chinese_ratio ≥ 0.95` 这种数字对模型没有可操作性——它不会去数自己的汉字占比。
按阈值分档渲染成可执行的指令，档位与阈值的对应关系写死在本模块，
**随配置变化，但不是把配置字面量贴进 prompt**。

决策日期：2026-08-22
"""

from __future__ import annotations

from a_rules.config import StyleConfig

__all__ = ["render_style_rules", "LANGUAGE_BANDS"]

#: 讲解语言一致性的分档。阈值 → 指令强度。
#:
#: 分档是刻意的：模型无法执行「汉字占比不低于 0.95」，但能执行
#: 「除专有名词外不要出现英文」。档位边界取自 v1.0 默认值 0.85 与
#: 配置扫描实验的 P4 点 0.95，中间留一档。
LANGUAGE_BANDS: tuple[tuple[float, str], ...] = (
    (0.95, "全程用中文讲解。**除专有名词与公式符号外不要出现英文**，"
           "需要给出英文原文时用括号紧跟在中文之后，不要整句英文。"),
    (0.90, "全程用中文讲解。英文只在给出专业名词原文时出现，且用括号紧跟中文之后。"),
    (0.00, "用中文讲解。英文可以出现在专业名词和必要的原文引用里。"),
)


def _language_rule(threshold: float) -> str:
    for floor, text in LANGUAGE_BANDS:
        if threshold >= floor:
            return text
    return LANGUAGE_BANDS[-1][1]


def render_style_rules(config: StyleConfig) -> str:
    """渲染成 markdown 列表。只含**激活**的维度。

    输出对同一份配置是确定的——它进 system prompt，而 system 要能被缓存，
    任何非确定性（集合遍历顺序、时间戳）都会让缓存每轮失效。
    """
    rules: list[str] = []

    # 详细程度（常驻）
    rng = config.paragraph_length
    rules.append(
        f"- 段落长度：每段控制在 {rng.min}–{rng.max} 字。"
        f"这是**平均**要求，不要靠拆行凑数——把内容讲完整，再决定分几段。"
    )

    # 讲解语言一致性（条件激活）
    if config.activates_chinese_ratio:
        rules.append(f"- 讲解语言：{_language_rule(config.chinese_ratio.min)}")

    # 引用长度（常驻，合规约束）
    rules.append(
        f"- 引用课件原文时，**单段引用不超过 {config.max_quote_chars} 字**，"
        f"其余内容用自己的话讲。这是版权约束，不可协商。"
    )

    # 术语处理（v1 下线；开启时才渲染）
    if config.activates_term_policy:
        rules.append(
            "- 专业术语首次出现时，按「中文译名（English original）」标注英文原文。"
        )

    return "\n".join(rules)
