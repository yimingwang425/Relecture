# 漂移实验（A 类维度版本）

PRD §8.2「漂移衰减曲线」的实现。**只含 A 类四维**——B 类维度与 judge 校准
等人工标注就绪后再做，因此本实验的结论只覆盖 A 类维度的漂移。

## 实验设计与其边界

| 项 | 取值 | 理由 |
| --- | --- | --- |
| 对照 | 有审查 / 无审查，各 20 轮 | §8.2 |
| 审查 agent v0 | 只查 A 类四维，零模型成本 | §7.2「生成方与审查方目标相反」，v0 做到最直白：审查方完全不调模型，没有自我合理化的空间 |
| 重生成上限 | 2 次 | §7.1 |
| 无审查组的指标 | **照常计算，只记录不干预** | 不算就没有对照 |
| **检索** | **不含。课件全量进 system prompt** | §8.2 要隔离的变量是「审查 agent 有无」。引入检索后两组可能取到不同片段，连输入都不一样，对照就废了。**结论仅适用于「课件全量在上下文中」这一条件** |
| 重生成方式 | 反馈驱动，不是重试 | 当前模型已移除 temperature，纯重试大概率复现同样的问题；把审查反馈作为一轮追加指令，模型才有依据修改 |
| 修正往返 | **不进对话历史** | 只留提问与最终输出，两组历史结构保持一致；否则第 k 轮的输入长度差异会变成审查之外的第二个变量 |

## ⚠️ temperature 在当前模型上不可用

Claude Opus 5 / Sonnet 5 **已移除 `temperature`、`top_p`、`top_k`，传入返回 400**。
「固定温度以控制随机性」在这一代模型上无法执行。

可固定并记录进实验元数据的是 **`effort`** 与 **`thinking`**（样本头写的
「Sonnet 5 Medium」对应 `effort: "medium"`，不是温度）。剩余随机性来自模型
采样本身，只能靠 sanity check 的重复采样观察，不能靠参数消除。

## Sanity check

两层，`python -m drift sanity` 一并跑：

1. **输入同一性（硬检查）**——两组第 1 轮的 system / messages / 模型参数逐字节
   相同，比对 sha256 指纹。确定性检查，抓实现 bug，不过就中止。
2. **输出同分布（观察）**——重复采样第 1 轮各 N 次，报告四维的均值与极差。
   n 通常只有 3–5，**这不是统计检验**，只能看量级异常；报告里据此写
   「未见系统性差异」而非「已验证同分布」。

## 用法

```bash
PY=../A类规则计算脚本/.venv/bin/python   # 复用 a_rules 的 venv

# 1. 抽课件全文（需要 pypdf，仅此一步）
$PY -c "from pypdf import PdfReader; import pathlib; \
  print('\n\n'.join((p.extract_text() or '') for p in \
  PdfReader('../测试集/MIT22_081JF10_lec01b.pdf').pages))" > config/courseware.txt

# 2. 写你的 20 个提问
cp config/questions.example.json config/questions.json && $EDITOR config/questions.json

# 3. 先用假模型跑通全链路（不调 API，确认 JSONL 结构、排序、成本日志）
$PY -m drift sanity --fake
$PY -m drift run --fake --out runs/smoke

# 4. 真实调用
export ANTHROPIC_API_KEY=...
pip install anthropic          # a_rules 本身仍是零依赖，这只是实验侧
$PY -m drift sanity            # 先单独跑 sanity
$PY -m drift run               # 正式实验
```

`--fake` 用确定性假模型，输出质量随轮次线性衰减。**它不是模拟真实漂移**——
曲线形状是写死的，只用来验证链路，不能当结论看。

## 输出

```
runs/<时间戳>/
├── metadata.json   模型参数、配置指纹、术语表版本与冻结状态、sanity 结果、
│                   以及「不含检索」「不含 B 类」「temperature 不可用」三条边界声明
├── rounds.jsonl    每轮一条完整记录 —— 漂移曲线的数据源
└── transcripts/    每轮最终讲解文本，轮次零填充命名（排序 = 轮次序）
```

`rounds.jsonl` 每条含：条件、轮次、提问、请求指纹、重生成次数、是否达标、
每次尝试的 completion（token / 成本 / 延迟 / stop_reason）与审查结果、
本轮 usage 汇总与成本、最终四维评估的完整 details。

成本按 claude-api 的公开价目折算，含 prompt caching 的 1.25×写 / 0.1×读。
Sonnet 5 按正价（$3/$15）而非 8 月底前的introductory价计——宁可高估，
也不要在 §8.4 的 <$0.05 判定上乐观。

## 不入库的文件

`config/courseware.txt`（课件全文，避免仓库里出现同一份材料的第二个副本）、
`config/questions.json`（你自己写的提问）、`runs/`。
