# CC 任务书 · F4-P2 · 前端 + 埋点

## 前置

P1 已完成并通过真库验收（`f6903b1`，真库 38 绿 0 红）。
`confusion_marks` 表已在真库、`POST /marks` 已上线、注入已接进 `/explain`。
**本轮只做 web 侧。**

---

## 只读这些文件，不要读其他

| 文件 | 读什么 | 为什么 |
| --- | --- | --- |
| `docs/F4/F4方案.md` | **只读 §3.2、§3.4** | 按钮形态、事件字段 |
| `f1_samples/README.md` | **只读 §一** 中的 F05 / F06 / F41 / **F54–F66** | 反馈按钮的既有映射与本轮口径（P1 实际写到 F66，13 条） |
| `docs/F4/F4-P1执行记录.md` | 全文 | 接口返回什么、`round_id` 从哪来 |
| `web/` | 自行定位：讲解页的四个反馈按钮组件、`createEventQueue`、事件类型定义处 | 改动点 |

**不要读** PRD、F1/F2/F3 方案、交接文档。需要的纪律抄在下面。

---

## 必须遵守的既有纪律

1. 不开分支，只用 main。commit 前缀 `feat(web)`。
2. **随机与副作用不要写在客户端组件的 `useState` 初始化里**——SSR 和 hydrate 会各跑一次，两次结果不同。本轮如无随机需求则不适用，但改动已有组件时不要引入。
3. **事件必须走 `createEventQueue`**（退避重试 + sessionStorage 跨页面存活）。不要直接发 POST：浏览器会自动重发幂等 GET，但**不会自动重发 POST**，丢了就没了。
4. 事件的 `user_id` / `arm` / `session_id` / `schema_version` / `created_at` / `deck_id` **是列，不在 payload 里**。写 payload 时不要重复塞。
5. 服务端比客户端旧要响亮失败。新增字段时确认 API 侧已经认得它。

---

## P1 已确认的接口形状（照这个写，不要猜）

**`/explain` 响应里的 `confusion` 字段：**

```
confusion: {
  injected: bool,              // 本轮有没有注入限定语
  note: string | null,         // 注入的那句原文
  marked_rounds: int,          // 命中了几条历史 mark
  matched_slice_keys: string[] // 命中的键，是本轮检索键的子集
}
```

**`POST /marks` 请求与响应：**

```
POST /marks   { round_id, slice_ids, question }     ← 路径无 /api/v1 前缀，与其余五条路由对齐

200 { round_id, created, mark_count_after, slice_count, events }
```

⚠️ **重复提交也返回 200，只是 `created: false`。** 前端**不要**为它分支——服务端刻意不返回 409，就是为了前端少一个它不关心的分支。

⚠️ `slice_ids` 传空数组会 **422**（一条空 mark 是死数据）。多传一个未知字段也会 422（`extra="forbid"`），不要往请求体里加字段。

---

## 做什么

### 1. 第五个反馈按钮

在讲解页每轮下方现有的四个反馈按钮区加第五个：**「这部分没看懂」**。不新建组件。

```
POST /marks  { round_id, slice_ids, question }
  成功 → 按钮变为「已记下」并 disabled（本轮内不可再点）
  失败 → 按钮恢复可点，不静默吞掉
```

`round_id` 与 `slice_ids` 来自本轮 `/explain` 的响应。

**不做取消**（F60）。**不做跨刷新回显**——刷新后按钮回到可点，重复提交由后端幂等挡掉。

### 2. 空检索轮不显示这个按钮

本轮检索为空时（`slice_keys` 为空），不渲染这个按钮。接口会 422，响应里 `confusion.skipped_reason` 说明原因。

⚠️ **这意味着这类情形下用户想标记却没得标，而我们看不见发生了多少次。**
**不为此加新埋点**——`retrieval_executed` 事件里已有 `slice_ids`，空检索轮占比本来就算得出来。
改为在人工验收清单里加一条观察项，用这句记下当前占比：

```sql
select count(*) as 检索轮总数,
       count(*) filter (where payload -> 'slice_count' is not distinct from '0'::jsonb) as 空检索轮
from public.style_events
where event_type = 'retrieval_executed';
```

### 3. 文案必须把它和「讲太深」分开（口径 F56）

本轮最容易出错的一处。两个按钮语义接近，**用户点错不会有任何指标变红**——配置被一路改深，而没人知道。

| 按钮 | 作用域 | 持久性 | 改配置 |
| --- | --- | --- | --- |
| 讲太深 | **全局**，深度轴下移一格 | 永久，影响之后所有讲解 | ✅ |
| **这部分没看懂** | **局部**，只影响本轮命中的那几片课件 | 下次讲到这部分时生效 | ❌ |

文案要把「以后都」和「这次这里」说开。措辞你定，但两个按钮的说明必须**放在一起能看出区别**，不要一个写在 tooltip 一个写在按钮上。

### 4. 埋点事件 `confusion_mark_created`

数据库侧 P1 已经建好（`f1_event_required_keys` 已认识它，真库确认 `event_types_known: 12`）。本轮只做前端发送。

| payload 必需字段 | 来源 |
| --- | --- |
| `round_id` | 本轮 `/explain` 响应 |
| `slice_ids` | 本轮 `/explain` 响应 |
| `mark_count_after` | `POST /marks` 的返回值 |

必需字段清单登记进**既有的**必需键出处，不要另开一份。

**这个事件不改配置**：`config_changed: false`，**不进「配置修改频率」的分子**（F41），形态与「例子没懂」（F06）一致。

### 5. web 测试

- 按钮点击 → 发出请求 → 成功后 disabled
- 请求失败 → 按钮恢复可点
- 重复提交返回 `created: false` 时，**UI 与首次成功一致**（不许多一个分支）
- 空检索轮 → 按钮不渲染
- 事件带齐三个必需字段
- 事件走的是 `createEventQueue` 而不是裸 POST（**把队列换成空实现，断言必须红**）

### 6. 人工验收清单

写进 `docs/F4/` 下一份 md。

**账号**：用 `f1-rls-a@example.com`，**不要新建一次性账号**。理由：验收要一门摄入过语料的课，而 `course_a`（`192ce225-acc7-4fc9-a200-79e57a0b0ae9`）就挂在这个账号下；换账号意味着重新摄入，要再花一次 embedding 的钱。这个账号本身就是测试账号，内测前整个删掉，marks 随 cascade 走。

**「请求里含不含限定语」的查法**：不需要查数据库，也不需要新增调试接口——`/explain` 响应里的 `confusion.injected` 与 `confusion.note` 就是现成的判据，浏览器 DevTools 的 Network 面板直接能看。清单里写明看哪个字段。

**清单要求**：

- **判据跑前定死**，不写「点几次看看」这种
- **标出执行顺序依赖**：要有 `round_id` 才能点按钮，所以必须先完成一轮讲解；注入效果在**第二轮**才看得到
- **标出不可逆步骤**（`confusion_marks` 不授 delete，点下去的标记删不掉）
- 至少覆盖：

  1. 点击后按钮变「已记下」；刷新后回到可点；再点一次 → 数据库里仍是**一行**
  2. 标记后再问一次会命中同一批切片的问题 → `confusion.injected == true`，`note` 是那句限定语
  3. 问一个**完全不同**、命中其他切片的问题 → `confusion.injected == false`（**这条是防注入恒触发的，不能漏**）
  4. 事件表里多了一行 `confusion_mark_created`，三个字段齐全
  5. 观察项：空检索轮占比（§2 那句 SQL）

---

## 不做

- 取消标记、已标记状态回显
- 「没看懂」的列表页 / 复习入口
- 为空检索轮加新埋点
- PRD 修改（P3）

---

## 交付

1. `web/` 代码改动，测试全绿（报出条数）
2. `docs/F4/` 下的人工验收清单
3. 一句话说明：第 5 项里「把队列换成空实现」是怎么让它红的
