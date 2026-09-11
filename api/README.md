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
| `POST /explain` | 一轮讲解。四条轴全部进 prompt（A 类 + B 类，口径 F45），详细程度同时驱动 `RuleReviewer` 的判定（口径 X05） |

## 四、⚠️ 阶段 3 的检索是**替身**，登记在案

`/explain` 目前用 `InMemoryRetriever` 喂固定的合成课件。
`InMemoryRetriever` 自己的文档写着「**它不假装自己是检索**……任何召回率类断言
都不得建立在它之上」。

所以：**本阶段不得出任何召回类结论**。用它做**风格**断言是合法的——
风格遵循度不依赖检索对不对。跨课召回那条验收（验收纪律 2）属阶段 5。

## 五、验收

`tests/` 的 26 条离线钉死三段：**库里那一行 → StyleConfig → prompt**，
外加**配置 → 判定**（同一段文本在 P0 与 P3 下结论相反，不需要模型参与）。

**第四段（模型服不服从 prompt）要真调模型**，在
`scripts/acceptance_p0_p3.py`，属人工验收：

```bash
.venv/bin/python scripts/acceptance_p0_p3.py \
    --base https://<railway-host> --supabase-url https://<ref>.supabase.co \
    --publishable-key sb_publishable_... --email <验收账号> --password <密码>
```

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

## 七、部署

Railway · 新加坡区 · `api/Dockerfile`（**构建上下文是仓库根**，
Root Directory 留 `/`，Dockerfile Path 填 `api/Dockerfile`）。

选型理由写在 Dockerfile 文件头。⚠️ **镜像尚未构建过**：本机 Docker daemon
没起来。已验的是 uvicorn 入口本身（`/health` 200、无 token 401、缺环境变量启动即失败）。
