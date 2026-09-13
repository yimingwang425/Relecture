# CC 任务书 · F4-P1 · 数据层 + 后端 + 口径

## 目标

把 F4 的第二件事（记录「没看懂」并在下次命中时注入讲解 prompt）在**后端与数据层**做完，
外加 F4 第一件事（跨课继承）的那条断言。前端不做，留给 P2。

---

## 只读这些文件，不要读其他

| 文件 | 读什么 | 为什么 |
| --- | --- | --- |
| `docs/F4/F4方案.md` | **全文** | 本轮的合同，口径与理由都在里面 |
| `f1_samples/README.md` | **只读 §一 口径表** | 全项目唯一一份口径表，本轮要续写 F54–F64 |
| `supabase/migrations/` | **最近一个 F1 迁移文件** | 学迁移的写法、RLS 策略与 cascade 的既有形态 |
| `supabase/verify_rls_local.mjs` | 全文 | 守卫与反向验证的既有形态，本轮照抄结构 |
| `api/` | 自行定位：`/explain` 路由、prompt 组装（`build_system` / 本轮消息拼装）、检索调用处、埋点写入处 | 注入点与 `slice_ids` 在这里 |

**不要读** PRD 全文、F1/F2/F3 的方案与实验报告、交接文档。需要的纪律已抄在下面。

---

## 必须遵守的既有纪律（不用去翻文档）

1. **不开分支，只用 main**。commit 用 `feat(api)` / `feat(supabase)` 这类既有前缀。
2. **禁止用 service role key 连 Supabase**。前端传 Supabase JWT 给 FastAPI，FastAPI 带该 token 访问，RLS 才有意义。`SUPABASE_SECRET_KEY` 不存在也不要加。
3. **pydantic 模型一律 `extra="forbid"`**。默认的 `ignore` 会静默丢字段，而一次参数被整个忽略的调用，在 HTTP 层和一次成功的调用长得一模一样。
4. **写测试替身时，照生产实现真的返回什么写，不照记忆写**。先跑一次真实现看返回类型，再写替身。
5. **埋点测试断言「表里多了一行」，不是断言「函数被调用」**。后者在写库失败时照样绿。
6. **每条守卫都要当场演示能变红**：把被测逻辑换成平凡实现，断言必须红。不会红的断言是空的，要在报告里逐条记录你是怎么让它红的。

---

## 做什么

### 1. 迁移：`confusion_marks`

新文件放 `supabase/migrations/`，命名沿用既有格式。

```sql
confusion_marks
  id          uuid  PK  default gen_random_uuid()
  user_id     uuid      REFERENCES auth.users(id) ON DELETE CASCADE
  round_id    uuid      -- 讲解轮标识，幂等键
  slice_ids   uuid[]    -- 本轮检索命中的全部切片
  question    text      -- 本轮提问原文，存下来但本轮不使用（口径 F59）
  created_at  timestamptz default now()
  UNIQUE (user_id, round_id)
```

- **RLS**：`user_id = auth.uid()`，**只给 select 和 insert**，不授 delete（口径 F60）。insert 的 `with check` 必须限制 `user_id = auth.uid()`。
- `slice_ids` 上建 GIN 索引（交集查询要用 `&&`）。
- **不加 `course_id` 列**（口径 F63）：切片经 `documents` 可 join 出课程，检索本就按课隔离。
- 迁移文件头写明本轮口径编号与日期，沿用既有文件头格式。

### 2. `round_id` 的来源

先查清楚 `/explain` 当前的响应里有没有一个稳定的轮标识。

- **有** → 直接用，并确保它返回给前端。
- **没有** → 在服务端生成一个 uuid，随讲解响应一起返回；同一次讲解重生成多次仍是同一个 `round_id`（重生成不计入轮次，轮的定义是「用户可见的一次输出」）。

把结论写进口径表。

### 3. API：`POST /api/v1/marks`

- 入参：`round_id` · `slice_ids` · `question`。
- 幂等：靠 `UNIQUE (user_id, round_id)`。重复提交返回 200 而不是 409，前端不需要区分。
- 返回：该用户当前的 marks 总数（`mark_count_after`，P2 的埋点要用）。
- 不做 GET、不做 DELETE。v1 不回显已标记状态，也不做取消。

### 4. 注入

链路：

```
提问 → 检索得到 slice_ids
      → 查本人 marks 中 slice_ids 与本轮有交集的行（一次 && 查询，零模型成本）
      → 交集非空 → 在【本轮用户消息】前缀注入限定语
      → 生成
```

- **注入位置在本轮消息，不进 system**（口径 F57）。system 段是按「会话内不变」设计的，marks 逐轮变化。
- **限定语原文**（口径 F58，一字不改）：

  > 用户此前看过这部分内容并标记为没看懂。换一个切入点重讲，不要照搬上次的表述。

  不要写成「讲得更详细」——那与 `paragraph_length` 判定方向相反，会推高重生成触发率。
- **不注入 `question` 字段**（口径 F59）。字段存着，本轮不读。

### 5. 核对 `slice_ids` 有没有落库

讲解轮的埋点事件 payload 里必须有非空 `slice_ids`。

- 有 → 加一条断言钉住它。
- 没有 → 补上（检索结果本来就在手里，零成本）。

**这是护栏指标「重复提问率」的唯一数据来源，漏了就再也补不回来。**

### 6. 跨课继承的断言（F4 第一件事）

不写功能代码，只加断言：

> 同一用户在两门不同课下各跑一轮，`build_system()` 产出的风格片段必须逐字节相同。
> **反向验证**：让配置读取带上 `course_id` 分行 → 断言必须红。

### 7. 守卫八条

每条都要有测试，且每条都要演示过变红：

| 守卫 | 反向验证方式 |
| --- | --- |
| 跨课继承 | 配置读取按 `course_id` 分行 → 必须红 |
| **注入会触发** | 造一条有交集的数据 → 本轮 messages 必须含限定语；删掉注入逻辑 → 必须红 |
| **注入不是恒触发** | 造一条**无交集**的数据 → 必须**不**注入；把交集判定换成恒真 → 必须红 |
| RLS 跨用户隔离 | 用 A 的 token 读 B 的 marks → 403 或空 |
| cascade | 删 `auth.users` 的行 → marks 必须消失；外键改 `no action` → 反向必须红 |
| 幂等 | 同一 `round_id` 提交两次 → 表里仍一行 |
| 埋点写库 | 断言表行数增加 |
| `slice_ids` 非空 | 置空 → 必须红 |

第三条最关键：**一个恒触发的限定语，和一个永远不触发的规则一样糟**，且更难发现——输出看起来完全正常，只是每轮都多讲一句。

### 8. 口径表 F54–F64

写进 `f1_samples/README.md` §一，沿用既有列格式（编号 / 口径 / 决策 / 理由 / 归属 / 日期）。
内容照 `docs/F4/F4方案.md` §五，**不要改写理由**。`round_id` 的来源结论另起一条编号续写。

### 9. 真库验证脚本

`api/scripts/verify_f4_marks.py`，形态参考同目录已有的验证脚本。要能自己答出来（不能靠手查数据库）：

- 迁移是否已应用（表、索引、RLS 策略、外键的 cascade 属性各查一次）
- 用测试账号插一条 mark，再用另一个账号查，必须查不到
- 同一 `round_id` 提交两次，表里仍一行
- 删测试账号 → marks 消失（**删之前先记数**，不先记的话「0 条」分不清是 cascade 生效还是本来就没有）

脚本要接 `--base` 与 `--expect-commit`，与既有脚本一致。

---

## 不做

- 前端任何改动（P2）
- PRD 修改（P3）
- 取消标记 / 概念抽取 / 按标记次数分档注入 / 遗忘曲线

---

## 交付

1. 迁移文件 + 后端代码 + 测试全绿（报出条数）
2. `api/scripts/verify_f4_marks.py`
3. `f1_samples/README.md` §一 新增 F54–F64
4. 一份简短的执行记录（放 `docs/F4/`）：八条守卫**各自是怎么被验证变红的**、`round_id` 的来源结论、`slice_ids` 原本有没有落库

## 交付后我要做的事（你在记录里写清操作顺序）

1. 在 Supabase 跑迁移
2. 跑 `verify_f4_marks.py`
3. 部署 Render

**迁移必须先于部署**。反了的话事件会被数据库拒绝，而接口照常返回 200——静默失败。
