# 多智能体链路 · 第一次真模型验收（2026-10-08）

原始记录：同目录 `agent_live_20261008.json`（提交 `b2fef60`）。脚本：`explain/scripts/agent_live_check.py`。
讲解模型 `claude-sonnet-5`；路由 / 改写 / 兜底判断 `claude-haiku-4-5-20251001`；风格档位 P2。
检索是**本地的**（Haiku 改写 + BM25 词法，无向量、无重排），比生产弱——工具调用频率不代表生产。

**合计 $0.1358**（按 `llm.PRICING` 折算），6 轮，每轮跑一次。

## 链路跑通了没有：跑通了

| 要证明的 | 结果 |
| --- | --- |
| 真模型会调工具、`strict: true` 的 schema 真 API 收 | ✅ 5 道课件题全部调了 `search_slices`，入参全部合法，0 个 `is_error` |
| 带 thinking 的多步对话，上一步内容块原样接回去 API 收 | ✅ 0 次回落（`mode` 全是 `agent`，`fallback_error` 全是 null） |
| 并行工具调用 | ✅ 5 道题都是**一步里发两次检索**（一轮工具往返），两条结果同一条消息交回 |
| 路由分得开寒暄与课件题 | ✅ 「谢谢，讲得很清楚！」→ `chat`，不调 Sonnet，$0.0022；其余 5 道 → `course` |
| 审查仍由代码强制 | ✅ 3/5 道触发一次重写（`reset{reason:"review"}`），全部最终达标 |

## 逐题

| 题 | 首轮兜底判断 | agent 的查询 | gold 页：首轮 → 最终 | 首字 | 关键路径 | 单轮 |
| --- | --- | --- | --- | --- | --- | --- |
| Q001 热为什么不能全变成电 | insufficient | `heat engine Carnot efficiency …`、`热机效率 卡诺循环 …`（0 片） | — → **p20** | 12.5 s | 14.6 s | $0.025 |
| Q013 反应堆的热怎么变成电 | insufficient | `nuclear power plant steam turbine …`、`thermal power plant heat to electricity …` | — → **p20, p27** | 7.1 s | 12.5 s | $0.025 |
| Q013 追问：效率能到多少 | partial | `热机效率 典型数值 …`、`nuclear power plant thermal efficiency 33%` | p20 → p20 | 7.6 s | 11.9 s | $0.033 |
| Q014 核反应比烧煤多多少倍 | insufficient | `nuclear fission energy per reaction …`、`energy released per fission MeV …` | — → **p28** | 5.3 s | 9.4 s | $0.023 |
| Q018 乏燃料怎么处理（无答案题） | insufficient | `spent nuclear fuel waste disposal storage`、`nuclear waste repository Yucca Mountain` | 无 gold | 6.2 s | 9.2 s | $0.028 |
| 寒暄 | —（chat） | — | — | 1.7 s | 0.7 s | $0.002 |

## 能说的与不能说的

- **能说**：本地这套弱检索下，首轮没取到 gold 页的 3 道题，agent 自己换成英文术语再查，**3 道都把 gold 页找回来了**；
  无答案题查了两次仍没有，讲解如实说「片段没覆盖」，没有编。
- **不能说**：「agent 让讲解变好了」。n = 5、每题一次、检索不是生产那一套（R17）。生产的首轮有向量一路与重排，
  首轮就取到 gold 页的比例高得多，agent 去查的频率与收益都要在生产上另测。
- **代价**：多一轮工具往返，首字 5–12.5 s、关键路径 9–15 s（§9.2 要求首字 < 3 s、完整 < 15 s）。
  首字超标主要来自「判断说不够 → 作废投机版 → agent 去查 → 再写」这条路；生产首轮判 `covered` 的轮次不走它。

## 观察到、本轮不修的（带触发条件）

1. **Q001 开头一句元评论**：「第20页有直接相关内容，可以回答。」——违反 system「不要描述片段本身」。
   1/5，n 太小，不为它改 prompt（改了也测不出有没有用）。**触发条件**：内测里同一形态在 agent 轮次中可数地多于固定流水线轮次。
2. **chat 轮 `accepted = false`**：寒暄回复两句话，段长落不进 P2 区间。设计如此（F110：审查照算、不据此重写），
   但**算风格遵循度时必须按 `agent_route = 'chat'` 排除这些轮**，否则寒暄会把遵循度拉低。已写进 F110。
