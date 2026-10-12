"""F3a · 讲解对话链路。

检索 → 讲解生成 → 风格审查（版权规则 + 风格审查 agent，`style_review`）→ 不合格重生成（上限 2）。

**v1 的风格遵循度只含三个维度**：详细程度 / 引用长度 / 讲解语言一致性
（PRD §3.2「风格遵循度的 v1 定义」）。术语处理已下线，冗余句式未实现。

核心以外的两个依赖是刻意隔开的：

- `retrieval`（numpy / openai）只被 `retrieval_adapter` 引入，
  核心链路对着 `context.Retriever` 协议编程
- `anthropic` 只在 `llm.AnthropicModel` 真正发请求时才 import

这样 `--fake` 全链路与全部单元测试都不需要网络、也不需要这两个包。
"""

from . import _paths  # noqa: F401  — 必须最先执行，注入 a_rules 路径

__all__ = [
    "context", "fake", "gap", "llm", "log", "observe", "pipeline", "prompt",
    "review", "style_prompt", "style_review",
]
