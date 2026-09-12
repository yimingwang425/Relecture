# api · 产品服务端（F1 → F3 接线）

**`style_config` 那一行第一次成为一次讲解生成的输入。** 在此之前，F1 与 F3 是
两条互不相接的半截链路：F1 存到库、渲染到屏幕；F3 从写死的配置点读配置，
只被扫描实验和单测调用过。

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env && $EDITOR .env

.venv/bin/python -m pytest                    # 26 条，零 API 成本
.venv/bin/uvicorn api.main:app_from_env --factory --reload
```

护栏跑在 web 那条链里：`cd ../web && npm run verify:api`。
**没有 `.venv` 它判红，不跳过** —— 理由见 `web/scripts/verify_api.mjs` 文件头。

---

## 一、这一跳为什么现在才建

F1 阶段 1.5 论证过：F1 全部读写落在一张表上，多一跳 FastAPI 只是把 RLS
从「数据库执行」变成「应用转发」，**不增加保障**。那条论证**到今天仍然成立**——
所以本服务不把 RLS 搬到应用层重做一遍。

它存在的理由是别的：`a_rules` 的审查判定与 `explain` 的 prompt 组装是 Python，
浏览器跑不了。F1 阶段 4 推迟过这一跳（口径 F39），理由是
「`prompt_profile_id` 只在分析时被消费」—— **那条理由对生成链路不成立**。

## 二、三条纪律

| | |
| --- | --- |
| **不持有 service_role key** | 服务只有 publishable key（本就公开）。所有数据库请求带**用户自己的 JWT** 打给 PostgREST，RLS 由数据库执行。配了 secret key **启动就失败**（口径 F40） |
| **验签写死 ES256，不读 token 头部** | 读 token 说自己用什么算法，就是把选择权交给攻击者。公钥从 Supabase 的 JWKS 取（2026-09-11 实测：ES256 非对称，**服务端不需要任何共享密钥**） |
| **拿不到 JWKS 时拒绝，不放行** | 跳过了却返回 200 的鉴权，与它要防的失败是同一类东西（同 F15 的 `--allow-offline`）。取不到公钥是 **503**，不是 401，更不是放行 |

「你没资格」（401）与「我这边坏了」（503）分开报：把取不到公钥报成 401，
用户会去重新登录而问题在服务端；反过来把过期 token 报成 503，用户会一直等。

## 三、端点

| | |
| --- | --- |
| `GET /health` | 不需要 token。只报服务状态与「模型 key 配没配」 |
| `GET /me/style` | 以用户身份读 `style_config`。读到的是自己那行**不是因为这里过滤了 `user_id`**，而是因为策略 `style_config_select_own` 在数据库里 |
| `POST /explain` | 一轮讲解。四条轴全部进 prompt（A 类 + B 类，口径 F45），详细程度同时驱动 `RuleReviewer` 的判定（口径 X05）。**阶段 5 起可选 `course_id`**（真检索那门课，见 §四）、**阶段 6 起可选 `session_id` / `turn_index`**（埋点，见 §七） |
| `GET /courses` · `POST /courses` · `POST /courses/{id}/documents` | 摄入，见 §六 |

## 四、检索（阶段 5）：**给了 `course_id` 就是真的**

`POST /explain` 的 `course_id` 可选，**两条路的语义完全不同，不许混**：

| | 后端 | 用途 |
| --- | --- | --- |
| 给了 `course_id` | `SupabaseRetriever`（`api/api/retriever.py`） | 真检索那门课的语料，RLS 由数据库执行 |
| 不给 | `InMemoryRetriever`（固定合成课件） | **验收纪律 1**：P0 vs P3 比的是同一份材料在两个档位下的段落长度，材料一变那个比较就不成立 |

**两条路必须在响应里分得开**：`retrieval.meta.backend` 原样回传
（`supabase` / `in_memory`）。压成一句「检索完成」的话，一次走替身的调用
和一次真检索长得一模一样，而「用合成课件答的」与「用你的课件答的」是两件事。

⚠️ **替身那条路上仍然不得出任何召回结论**——`InMemoryRetriever` 自己的文档写着
「它不假装自己是检索」。用它做**风格**断言合法。

**验收纪律 2 在真库上达成**（2026-09-12，16 绿 / 1 红）：
`supabase/verify_retrieval_prod.py`。那 1 条红是迁移未在生产执行，见 §八。

## 四之二、摄入链第一次真跑（2026-09-12）

`api/scripts/ingest_real.py` —— 真 PDF → `/courses/{id}/documents` → **真 OpenAI** → 真库。
**27 绿 / 0 红。** 在这一发之前能说的只有「schema 接得住手写的向量」。

它抓出了三个真 bug，全部是「替身照我的记忆写，而不是照生产实现真的返回什么」
（口径 **F49**，详见 `docs/F1/F1进度.md` §十一之六）。
**pytest 在它们存在时是全绿的** —— 所以「第一次真跑」是一条交付物，不是收尾确认。

## 五、验收

`tests/` 的 26 条离线钉死三段：**库里那一行 → StyleConfig → prompt**，
外加**配置 → 判定**（同一段文本在 P0 与 P3 下结论相反，不需要模型参与）。

**第四段（模型服不服从 prompt）要真调模型**，在
`scripts/acceptance_p0_p3.py`，属人工验收：

```bash
.venv/bin/python scripts/acceptance_p0_p3.py \
    --base https://<service>.onrender.com --supabase-url https://<ref>.supabase.co \
    --publishable-key sb_publishable_... --email <验收账号> --password <密码>
```

⚠️ **Render 免费层 15 分钟无请求即休眠，冷启动 30s+。** 脚本先打 `/health` 唤醒，
**最多重试 3 次**（`scripts/prod_verify.py` 的 `wake_service()`，两个验收脚本共用一份）。
冷启动超时**不是失败**；但**重试 3 次仍不通就是失败**——无限重试会把
「服务真的挂了」变成「脚本一直在转」。唤醒**不计入绿数**。

⚠️ **判据不能只写「各自落在各自区间」**：P0 是 30–120、P3 是 15–40，
**两个区间在 30–40 上重叠**——一段平均 35 字的讲解同时满足两者，于是一个
把配置整个忽略、两次返回同一段文字的实现也能全绿。故判据是三条：
两个区间各自达标 **+ `mean(P0) > mean(P3)`（方向）**，外加允许控制
「两次的 `config_fingerprint` 不同」。

## 六、摄入（阶段 4）

`POST /courses` · `GET /courses` · `POST /courses/{id}/documents`。

**`parse-service` 并入本服务**：解析 → 切片 → 嵌入 → 落库对用户是**一次动作**，
拆两个服务意味着整份 PDF 多走一次网络、两套部署、两条 §13.3 记录。
连带后果是 `/parse` 从「无鉴权无状态」变成要鉴权 —— **那本来就得改，它现在谁都能调**。

| | |
| --- | --- |
| **真解析、真切片、假嵌入** | 断言喂的是仓库里的真 PDF，走生产的 `parse_pdf` / `slice_document`。嵌入是唯一要出境的一步（PRD §13.3 **第二次出境**），所以注入 |
| **一份文档是事务边界** | `compose()` 的 boilerplate 检测是**整份文档范围**的，嵌入不能增量 |
| **23505 翻译成人话** | 唯一索引 `(owner, course_id, sha)` 是**硬失败不是提示**。不翻译的话用户看到的是数据库错误码 |
| **五个口径版本列** | 含 `split_threshold_chars`——它是**改变切片结果的输入**，今天恒为 `None` 所以看不出问题，一旦按 embedding 窗口注入值，新旧切片就再也分不开 |

⚠️ **受限页排除对真实用户恒不触发**：`RESTRICTED_PAGES` 是按测试集文件名写死的清单
（以 `SOURCES.md` 为准）。如实登记 —— 不写成「受限页已排除」，那是对测试语料成立的句子。

## 七、埋点（阶段 6）· §13.0 的四个讲解事件

`api/api/events.py` —— `explain_turn` / `style_review_result` /
`regeneration_triggered` / `retrieval_executed`。

**在 Python 侧发，而 F1 那五个在 TS 侧发**（口径 **F51**）：判据是**数据在哪**。
这四个的全部字段都产生在那一次 `run_round()` 里（token 四类分列、成本与价目、
逐维得分、检索分数分布），**浏览器一个都不知道**；让前端发就得先序列化过去再发回来，
而且前端能改这些数字。

| | |
| --- | --- |
| **`session_id` 缺了就不发**，不现编 | 现编的话每一轮变成一个独立会话，而「平均会话轮次」是北极星指标——那个数会静静变成 1.0 附近。少发事件是可见的损失，编一个 session 是不可见的错数 |
| **null 有含义的字段不进必需清单**（口径 **F50**） | `f1_event_payload_ok` 把 JSON null 判成「没有」。`all_activated_passed`（D42）与 `first_token_latency_ms`（§9.2 不塞假数）进了清单会让**一条完全正确的事件被静默拒掉** |
| **失败不打断讲解，但也不静默**（F42） | 本次请求内重试（阶梯 200/700ms，比浏览器那份短——用户正在等这一屏），结果进响应的 `events` 字段 |

## 八、部署

**Render · 新加坡区** · `api/Dockerfile`。填法、环境变量、冷启动行为见
**`docs/F3/部署_Render.md`**（Root Directory **留空**，Dockerfile Path `api/Dockerfile`）。

平台从 Railway 换成 Render 的理由是**账号级的不是技术的**；Dockerfile 头部
那四条技术理由原样保留，对 Render 逐条同样成立。

### 构建标识（口径 F17）—— **验收前先核对你在跟哪个构建说话**

`/health` 的 `build_commit` + **每个响应**（含错误响应）的 `x-build-commit` 头。
Render 自动注入 `RENDER_GIT_COMMIT`；本机构建传一次：

```bash
docker build -f api/Dockerfile --build-arg BUILD_COMMIT=$(git rev-parse HEAD) -t f1-api .
```

**取不到时是 `null`，不是 `'unknown'`** —— 两者在验收里含义相反，见 `api/api/build.py`。

⚠️ 这一条是被一次真实误读逼出来的：2026-09-12 Render 首次部署后验收第 1–3 节全绿，
而线上跑的是**阶段 4 的旧构建**。F17 此前只在 web 侧落地过。
配套收紧了 `ExplainRequest` / `CourseRequest` 的 `extra` 到 **`forbid`**（口径 **F52**）：
旧构建会**静默丢掉**它不认识的 `course_id` 并返回 200 + 合成课件。
全过程见 `docs/F1/F1进度.md` §十二。

### ✅ 镜像已构建（2026-09-12，第一次）

```
docker build -f api/Dockerfile -t f1-api .       887MB
docker run（缺环境变量）  → 启动即失败并点名变量   ✅
docker run（配全）        → /health 200 · /me/style 401  ✅
```

**构建 + 启动当场抓出一个只有真构建才会出现的 bug**：
`COPY slicer /srv/slicer` 让项目目录落在 `sys.path` 第一位（uvicorn 的 cwd），
它没有 `__init__.py` → 被当成命名空间包，把 editable 安装整个盖掉
（`unknown location` 就是那个签名）。本机跑不出来，因为 cwd 是 `api/`。
修法与机制写在 Dockerfile 那几行的注释里。

### ⬜ 仍未做

| 项 | 状态 |
| --- | --- |
| Render 部署 | **未部署。** 部署文档是填法说明，不是执行记录 |
| 迁移 `20260912120000_f3_explain_events.sql` 在生产执行 | **未执行。** ⚠️ **它必须先于这个构建**——反了的话四个事件全部写不进去（`PGRST204`），而讲解本身照常返回 200 |
| 验收纪律 1（P0 vs P3 真调模型） | 未跑。要一个部署地址 |
