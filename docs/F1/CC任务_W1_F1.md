# CC 任务 · W1 · 骨架部署 + F1 风格获取

## 背景

个人 0→1 项目「AI 课件讲解助手」。F2（课件解析/切片/检索）与 F3（讲解链路/风格审查/兜底判断）
已完成，全部是 Python，800+ 条测试，跑在本地脚本上。

**现在开始全栈开发，本次是第一个垂直切片。** 10 月 10 日上线，剩 5 周。

技术栈已定：**Vercel（Next.js）+ Railway（FastAPI，Python）+ Supabase（Postgres/Auth/RLS）**。
本次**不涉及 Railway**——F1 全是读写一张表，前端直连 Supabase 即可。

---

## 开工前先读

按顺序读，读完再动手：

1. `PRD.md` §5.1（F1 定义）、§8.1（风格维度）、§13.0（埋点事件模型）、§12.1（版权约束）
2. `项目交接_AI课件讲解助手_F3完成.md` §〇 状态速查、§1.6、§1.7
3. `F1方案.md`（本次的完整方案，口径 F01–F07）
4. `A类规则计算脚本/README.md` 的 `StyleConfig` 字段与 `CONFIG_VERSION`
5. `A类规则测试总结.md` §五（七条通则）——本项目的工程纪律，尤其第六、七条

若路径与上面不符，先 `ls` 定位，**不要猜**。

---

## 不可违反的前提

| # | 约束 | 原因 |
| --- | --- | --- |
| 1 | **不改任何已有 Python 模块**（`a_rules` / `explain` / `retrieval` / `parse-service`） | 改了等于让 F2/F3 的实验结论全部重验 |
| 2 | **禁止使用 Supabase service role key** | RLS 是 §12.1 的核心合规承诺，绕过它是合规问题不是 bug |
| 3 | **配置指纹不在 TypeScript 侧计算** | TS 一份 Python 一份必然漂，本项目已踩过同类坑 |
| 4 | **样例课件必须自写合成内容** | §12.1：对外展示一律用自写合成课件，不得用第三方课件 |
| 5 | **每阶段做完立即部署到生产验证**，不在本地攒 | 三方接缝是本次唯一的 unknown |

---

## 阶段 1 · 表与 RLS

建 `style_config` 表：

```sql
create table style_config (
  user_id       uuid primary key references auth.users on delete cascade,
  detail_level  text not null default 'P0',              -- P0|P1|P2|P3
  use_examples  text not null default 'more',            -- more|fewer
  structure     text not null default 'conclusion_first',-- conclusion_first|buildup_first
  depth         text not null default 'why',             -- what|why|derivation
  f1_arm        text not null,                           -- pairwise|text
  onboarded_at  timestamptz,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);
```

**表里刻意不存的东西，不要加回来**：

- `chinese_ratio_min`（恒 0.85）、`max_quote_chars`（恒 100）——常量，存了就会漂
- `config_fingerprint` / `prompt_profile_id` / `metric_set_id`——Python 侧现算
- 配置历史表——修改次数从埋点事件反推

加 CHECK 约束限定取值域。RLS：`user_id = auth.uid()`，
**select / insert / update 三条策略，不要 delete 策略**（注销走 cascade）。

### 验收（必须在生产环境上做，本地绿不算）

用账号 A 的登录态去读账号 B 的行 → **返回空**。

---

## 阶段 2 · 样例对

### 2.1 合成课件片段

自写一段约 400–600 字的中文课件内容，要求：有一个明确概念、有机制说明、可举例。
主题自选（建议与现有测试集不同的领域，避免与 22.081J / 14.129 混淆）。
写进 `content/synthetic_deck.md`，文件头注明「自写合成内容，用于 F1 样例，非第三方课件」。

### 2.2 八对样例

全部基于同一段合成课件生成，**预生成为静态 JSON**（不现场调模型）。

| 目标轴 | 对 |
| --- | --- |
| 详细程度 | (P0,P3) · (P0,P1) · (P2,P3) |
| 举例 | (more, fewer) |
| 结构 | (conclusion_first, buildup_first) |
| 深度 | (what, derivation) · (what, why) · (why, derivation) |

档位参数（F3a 实测点位，平均段落字数区间）：
`P0` 30–120 · `P1` 40–90 · `P2` 25–55 · `P3` 15–40

**口径 F01 · 每对只在目标维度上不同。** 非目标维度一律取默认值
（详细程度 P0 / 举例 more / 结构 conclusion_first / 深度 why）。
这是通则二：若两个样例同时在两维上不同，用户选了 A 你不知道他选的是哪一维，
而 F1 的全部产出就是这个归因。

JSON schema 自行设计，须含：`pair_id` · `target_dimension` ·
两个 sample 的 `sample_id` / `variant_label` / `text`。

### 2.3 自动核对脚本（这是守卫，不是可选项）

写一个脚本，调用**现有的** `a_rules` 计算每个样例的 A 类三维指标，断言：

- 详细程度那三对：两个样例的平均段落字数落在各自档位区间内
- **其余五对：两个样例的平均段落字数差异必须小于阈值**（阈值自定，写明依据）

**反向验证**：故意造一对在两个维度上都不同的样例，跑脚本 → **必须红**。
不会红的守卫是空转的守卫（通则七 #4）。

B 类维度无法自动核，由我人工核，你把待核清单输出出来。

---

## 阶段 3 · 三个页面

| 路由 | 内容 |
| --- | --- |
| `/onboarding/style` | 成对比较，6 步，两张卡 + 进度 |
| `/onboarding/style-text` | 对照臂：一个文本框（A5 的 AB 对照组） |
| `/settings/style` | 当前配置 + 手动改档 |

### 分支逻辑（6 步，固定顺序）

```
#1 详细程度  P0 vs P3
     选 P0 → #2 P0 vs P1   → 结果 P0 或 P1
     选 P3 → #2 P2 vs P3   → 结果 P2 或 P3
#3 举例      more vs fewer
#4 结构      conclusion_first vs buildup_first
#5 深度      what vs derivation
     选 what       → #6 what vs why
     选 derivation → #6 why vs derivation
```

**AB 分组**：注册时按 `hash(user_id)` 定 `f1_arm`，写死不再变。
`pairwise` 进 `/onboarding/style`，`text` 进 `/onboarding/style-text`。

对照臂的「文字 → 配置」需要一次模型调用，走 Next.js route handler
（**API key 只在服务端，前端不碰**）。输出用结构化输出约束到合法取值域，
取值不合法即报错重试一次，不做「就近解释」。

UI 用 Next.js + Tailwind + shadcn/ui。**不追求好看**——目标 30–50 个真实用户。

---

## 阶段 4 · 反馈接口与埋点

### 反馈映射（口径 F05–F07）

| 按钮 | 行为 |
| --- | --- |
| 太啰嗦 | 详细程度轴下移一格（P0→P1→P2→P3） |
| 讲太深 | 深度轴下移一格（derivation→why→what） |
| 讲太浅 | 深度轴上移一格 |
| **例子没懂** | **不改配置**，只触发本轮重生成；`config_changed: false` |

**轴端点**：已在 P3 再点「太啰嗦」→ 配置不变，返回 `config_changed: false`，
前端给一句说明。**不要静默吞掉**——静默吞掉会让用户以为生效了，
也会在埋点里多一次假的配置变更。

按钮 UI 属讲解页（W3 做），本次只做**配置更新逻辑 + 接口 + 测试**。

### 四个埋点事件

通用字段照 `PRD.md` §13.0（含 `event_id` / `event_ts` / hashed `user_id` /
`schema_version` 等）。

| 事件 | 关键字段 |
| --- | --- |
| `style_pair_shown` | `pair_id` · `target_dimension` · `comparison_index` · `arm` |
| `style_pair_chosen` | `pair_id` · `chosen_sample_id` · `target_dimension` · `comparison_index` · `decision_latency_ms` |
| `style_config_set` | `source`(onboarding/feedback/manual) · `changed_fields` |
| `style_feedback_click` | `feedback_type` · `config_before` · `config_after` · `config_changed` |

> 这批 schema 是设计出来的，**不是从已落盘数据反推的**（与 §13.0 那四个事件来源不同）。
> 在文件里注明这一点，内测第 1 周须复核。

### 埋点的测试怎么写（重要）

**断言「事件表里多了一行且字段正确」，不是断言「写入函数被调用」。**
后者在写库失败时照样绿——这正是通则七 #7 的形态
（59 条测试全走假模型，真实方法名从没被断言碰过）。

---

## 明确不做

| 项 | 触发条件 |
| --- | --- |
| 主动选题（后验估计 + 信息增益选下一对） | 维度数或比较预算显著变大时 |
| `chinese_ratio` 开放给用户 | P4 的三口径分歧收敛后 |
| B 类维度进审查判定 | §8.5 judge 校准完成（v1.1） |
| 样例现场生成 | 有证据表明「用自己的课件」显著影响选择时 |
| FastAPI / Railway | W2（课件上传时） |
| 讲解页、流式、句子级审查 | W3 |

---

## 工作纪律

1. **先说方案再动手。** 每个阶段开始前，把你要做什么、口径怎么定、验收标准是什么说一遍，
   我确认后再写代码。
2. **改了口径要写下来**，进对应 README 的口径决策表（编号 F01 起），
   带**理由 / 日期 / 归属**。体例照 `explain/README.md` 的 X01–X12。
3. **每条守卫都要能被反向验证变红。** 写完一个测试问自己：
   把被测逻辑换成平凡实现，它会不会红？不会红就是空的。
4. **发现方案里的错就直接说**，不要照做。本项目已有 5 次「被数据推翻的判断」和
   9 个通则七实例，其中两个来自评审指定的机制。方案错了比代码错了贵。
5. 每阶段完成后部署并给出**生产环境上的**验收结果。
