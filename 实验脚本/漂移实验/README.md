# 漂移实验（A 类维度版本）

PRD §8.2「漂移衰减曲线」的实现。**只含 A 类四维**——B 类维度与 judge 校准
等人工标注就绪后再做，因此本实验的结论只覆盖 A 类维度的漂移。

## 实验设计与其边界

| 项 | 取值 | 理由 |
| --- | --- | --- |
| 对照 | 有审查 / 无审查，各 20 轮 | §8.2 |
| 重复 | **每条件 3 条独立轨迹** | 当前模型无 temperature，采样随机性无法消除；每条件每轮 n=1 时无法区分漂移与噪音 |
| 审查 agent v0 | 只查 A 类四维，零模型成本 | §7.2「生成方与审查方目标相反」，v0 做到最直白：审查方完全不调模型，没有自我合理化的空间 |
| 重生成上限 | 2 次 | §7.1 |
| 无审查组的指标 | **照常计算，只记录不干预** | 不算就没有对照 |
| **检索** | **不含。课件全量进 system prompt** | §8.2 要隔离的变量是「审查 agent 有无」。引入检索后两组可能取到不同片段，连输入都不一样，对照就废了。**结论仅适用于「课件全量在上下文中」这一条件** |
| 重生成方式 | 反馈驱动，不是重试 | 当前模型已移除 temperature，纯重试大概率复现同样的问题；把审查反馈作为一轮追加指令，模型才有依据修改 |
| 修正往返 | **不进对话历史** | 只留提问与最终输出，两组历史结构保持一致；否则第 k 轮的输入长度差异会变成审查之外的第二个变量 |

## 重复必须是整条轨迹的重复

漂移是一条对话轨迹随轮次演化出来的性质。在同一条对话里对某轮采样 3 次再挑
一条继续，轨迹仍然只有一条——重复没有统计意义。所以每个 replicate 是**一次
独立的 20 轮完整对话**，各自维护自己的历史。

`3 × 2 × 20 = 120` 次生成是**下限**，重生成会在此之上追加（上限 ×3）。

**误差带取全距（min–max），不取 ±1SD。** n=3 时样本标准差本身极不稳定，
用 ±1SD 作图会暗示一个并不存在的分布假设。标准差仍输出，但只作参考量。

聚合结果写入 `summary.json`，每行 `(条件, 轮次, 维度)` 带均值、全距、
**有效观测数**、达标数。有效数必须看——某些 replicate 可能因分母护栏
（`term_min_occurrences`）算不出数，只看「达标数/重复数」会把 1 个观测
误读成 3 个。

## ⚠️ 写提问时请注意分母护栏

`term_min_occurrences=5`：某轮的首现术语数少于 5 时，术语维度不出数值
（标 `low_confidence`）。六轮探路样本的首现数是 29 / 4 / 13 / 1 / 15 / 10
——**六分之二会被丢掉**。三条轨迹里丢掉两条，那一轮就只剩一个观测，没有
误差带可言。

所以 20 个提问最好每个都能牵出 5 个以上不同术语（问「某个流程如何运作」
比问「某个词是什么意思」更容易达到）。或者为实验单独调低 `term_min_occurrences`
——但那是口径改动，配置指纹会变，必须写进实验记录。

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
$PY -m drift run               # 正式实验（3 replicate × 2 组 × 20 轮）
$PY -m drift run --repeats 1   # 只想快速试一轨时
```

**成本估算**（claude-sonnet-5 正价，课件约 5K token，单轮输出约 1300 token）：

| 缓存策略 | 6 条轨迹 × 20 轮 |
| --- | --- |
| 只缓存 system | $7.31 |
| **再缓存对话历史（默认）** | **$3.66** |
| 含重生成（×1.4 估） | ~$5.13 |

对话历史逐轮增长，20 轮下来它远比课件本身贵——只缓存 system 会让历史每轮
按全价重算一遍。`cache_conversation` 默认开，断点打在最近一轮消息末尾。

`--fake` 用确定性假模型，输出质量随轮次线性衰减。**它不是模拟真实漂移**——
曲线形状是写死的，只用来验证链路，不能当结论看。

## 输出

```
runs/<时间戳>/
├── metadata.json   模型参数、配置指纹、术语表版本与冻结状态、sanity 结果、
│                   以及「不含检索」「不含 B 类」「temperature 不可用」三条边界声明
├── rounds.jsonl    每轮每 replicate 一条完整记录
├── summary.json    按 (条件, 轮次, 维度) 聚合的均值与全距 —— 作图数据源
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
