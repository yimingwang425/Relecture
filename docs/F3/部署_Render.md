# 部署 · Render（产品服务端 `api/`）

> **平台从 Railway 换成 Render（2026-09-12）。**
> 理由是**账号级的，不是技术的**：Railway 免费层已被另一个项目占用，
> 新建 project 要付费。
>
> `api/Dockerfile` 文件头那**四条技术理由原样保留**，因为它们对 Render 逐条同样成立：
> ① 原生依赖（PyMuPDF + numpy）要普通容器；② 生成带最多 2 次重生成，
> 请求轻松超 60s；③ 部署地要新加坡，Render 有 **Singapore** 区；
> ④ 这一跳真的需要跑 Python，与平台无关。
> **换平台不推翻任何一条已有论证，只是换了一个满足它们的执行者。**
>
> PRD §13.3 的「应用服务器部署地」已同步改为 **Render · 新加坡**。
> 出境次数与出境内容**一个字都没变**（仍是两次，仍含课件原文）——
> 换的是托管商，不是数据流。

---

## 一、填法（这一屏最容易填错的是前两行）

新建 **Web Service** → 连仓库 → 按下表填：

| 字段 | 填什么 | 填错会怎样 |
| --- | --- | --- |
| **Language / Runtime** | **Docker** | 选 Python 的话 Render 会去找 `requirements.txt`，而本服务没有——它的依赖写在 Dockerfile 的 `pip install` 那一行 |
| **Root Directory** | **留空**（= 仓库根） | ⚠️ **最容易填错的一个。** 直觉会填 `api`，而那样构建上下文就变成 `api/`，`COPY explain /srv/explain` 当场失败——`explain` 是 `api/` 的**兄弟目录**，不在它下面 |
| **Dockerfile Path** | `api/Dockerfile` | 与上一行配套：上下文在仓库根，Dockerfile 在 `api/` 下 |
| **Region** | **Singapore** | PRD §13.3 写着应用服务器在新加坡。选错区 = 改了一条已写进隐私政策的事实 |
| **Instance Type** | Free（内测期） | 见 §三的休眠行为 |
| **Health Check Path** | `/health` | 它不需要 token，且**只报服务状态与「模型 key 配没配」**，不报任何项目信息 |

> **为什么 Root Directory 留空而不是填 `api`**：本服务用路径注入引同仓库的
> `explain` / `a_rules` / `retrieval`（见 `api/api/__init__.py`），
> 并 `pip install -e` 装 `slicer` 与 `parse-service`。这五个包全部是 `api/` 的兄弟。
> 构建上下文必须能看见它们，所以上下文是仓库根。
>
> 这一条在 Railway 上是同一句话（`Root Directory` 留 `/`），
> 只是 Render 的表单里那一格**默认就是空的**，填了反而错。

### 环境变量

按 `api/.env.example`：

| 变量 | 说明 |
| --- | --- |
| `SUPABASE_URL` | |
| `SUPABASE_PUBLISHABLE_KEY` | **publishable key**，与前端同一个 |
| `ANTHROPIC_API_KEY` | 缺它 `/explain` 返回 503 并点名是服务端未配置，不静默失败 |
| `OPENAI_API_KEY` | 摄入那一步要（`retrieval.embed.OpenAIEmbedder` 从环境读） |
| `CORS_ALLOW_ORIGINS` | **上传页要它。** 逗号分隔的前端来源（Vercel 域名 + `http://localhost:3000`）。⚠️ **不配 = 空清单 = 浏览器一律被挡**，不是默认放开；写 `*` **服务启动就失败**。前端在浏览器里直连本服务，因为 Vercel 的 serverless 请求体上限 4.5MB 而上传上限 50MB |
| `RENDER_GIT_COMMIT` | **不用配**，平台自动注入。它就是 `/health` 的 `build_commit`（口径 F17） |

前端那一侧配套要配 `NEXT_PUBLIC_API_BASE_URL`（指向本服务），见 `web/.env.example`。
**两边都配好之前，`/courses` 那一页会点名说缺哪一个**，而本站其余页面不受影响
（它们直连 Supabase）。

⚠️ **`SUPABASE_SECRET_KEY` 不在这张表里，也不该被加进去**（口径 F40）。
`load_settings()` 见到 `sb_secret_` / `service_role` 形态的 key **启动就失败**——
不是警告。拿 service_role 跑等于「以为有 RLS，其实没有」，而那不会报错。

`PORT` **不用填**：Render 会注进来，`CMD` 里是 `${PORT:-8000}`。

---

## 二、验证部署成功（三步，顺序不能换）

**第 0 步：核对你在跟哪个构建说话**（口径 F17）。这一步在其余全部之前。

```bash
curl -sS https://<service>.onrender.com/health
# {"status":"ok","model_key_configured":true,"build_commit":"<sha>"}

curl -sSI https://<service>.onrender.com/health | grep -i x-build-commit
# x-build-commit: <sha>       ← 每个响应都有，401/404 也有

curl -sS -o /dev/null -w '%{http_code}\n' https://<service>.onrender.com/me/style
# 401  ← 没带 token 必须被拒；200 说明鉴权根本没跑
```

`build_commit` 由 Render 自动注入的 `RENDER_GIT_COMMIT` 给。
**它是 `null` 就别解释后面任何一条验收结果**——没有构建标识，
「N 条绿」说不出是哪一版的 N 条绿。

> ⚠️ **这一步是被一次真实误读逼出来的（2026-09-12）。** 第一次部署后
> 验收脚本第 1–3 节全绿，而线上跑的是**阶段 4 的旧构建**（阶段 5–6 的改动
> 当时还没提交）。那 8 条验的是 `match_slices` 这一层，**它在旧构建里也是对的**，
> 于是「8 条绿」被读成了「这一版部署成功」。
> 而旧构建会**静默丢掉**它不认识的 `course_id`（pydantic 的 `extra` 默认 `ignore`）
> 并返回 200 + 合成课件。详见 `docs/F1/F1进度.md` §十二。

第三步是真验收，见 `api/README.md` §五 与 §八：

```bash
cd supabase && python3 verify_retrieval_prod.py \
    --base https://<service>.onrender.com --expect-commit <sha>
```

`--expect-commit` 给了就断言相等；不给只打印，**那时请自己核对那一行**。

### ⚠️ 部署顺序：迁移先于构建

`supabase/migrations/` 里的迁移**必须先在 SQL Editor 跑完**再部署这个构建。
反了的话这一版的四个讲解事件会全部写不进去（`PGRST204 column not found`），
而**讲解本身照常返回 200**——埋点失败不打断讲解（口径 F42），
所以这个错不会以「服务挂了」的样子出现。

> **`/health` 返回 200 ≠ 配置正确。** 它只说进程活着。
> 「连的是另一个 Supabase 项目」这种错**不会**让 `/health` 变红——
> 挡它的是 `create_app()` 在导入时 `load_settings()`：**缺环境变量启动就炸**。
> 所以真正该看的是 **Render 的 Logs 里服务有没有起来**，不是 `/health` 的颜色。

---

## 三、⚠️ 免费层会休眠，而冷启动**不是失败**

**Render 免费层 15 分钟无请求即休眠，再次访问冷启动 30 秒以上。**
这是 Railway 没有的行为，两个后果：

1. **验收脚本的第一发请求可能超时。** 把它当失败的话，得到的是一条假红——
   而假红比没有断言更糟：它会让人去查一个不存在的 bug，
   下一次真红时又会被当成「又是冷启动吧」。
2. **重试必须有上限。** 脚本里是 **3 次**。
   无限重试会把「服务真的挂了」变成「脚本一直在转」，
   那是拿一个响亮的失败换一个安静的失败（同 F16 反对的那件事）。

处置落在两个脚本里，**共用同一份实现**（`scripts/prod_verify.py` 的 `wake_service()`）：

- 先打 `/health` 唤醒，超时重试，**最多 3 次**，每次之间等 20 秒；
- 打通了才开始跑判据；
- 3 次仍失败 → **判失败并退出**，打印的是「唤醒失败」而不是某条断言红了——
  两者的处置完全不同（一个去看 Render 的 Logs，一个去看代码）。

```
唤醒 https://xxx.onrender.com …
  第 1 次：超时（免费层冷启动，30s+，不是失败）
  第 2 次：200  ← 已唤醒，耗时 34.2s
```

> **唤醒这件事本身不计入绿数。** 它不是一条断言——
> 「服务能被唤醒」不证明任何业务行为对。
> 计进去的话，一份全部业务断言都红了的报告仍然有一条绿。

---

## 四、与本地构建的关系

**部署前先在本机构建一次**：

```bash
# ← 注意末尾那个 `.`：上下文是仓库根。BUILD_COMMIT 传一次，
#   验收脚本才问得出「我在跟哪个构建说话」（线上由 Render 自动注入，不用传）
docker build -f api/Dockerfile \
  --build-arg BUILD_COMMIT=$(git rev-parse HEAD) -t f1-api .
```

理由：Render 的构建日志是**最难查错的地方**——它在远端、有缓存、
失败时只给你一段尾部输出。本机构建一次，同一个 Dockerfile 的问题在本机就暴露了。

本机跑起来看一眼（缺环境变量应当**启动即失败**，这是设计）：

```bash
docker run --rm -p 8000:8000 \
  -e SUPABASE_URL=... -e SUPABASE_PUBLISHABLE_KEY=... f1-api
curl -sS localhost:8000/health
```

---

## 五、状态

| 项 | 状态 |
| --- | --- |
| `api/Dockerfile` 本机构建 | ✅ 2026-09-12 首次构建成功，见 `docs/F1/F1进度.md` §十一 |
| Render 部署 | ✅ **2026-09-12 部署成功**（`/health` 200、`model_key_configured: true`、`/me/style` 无 token 401） |
| 迁移在生产执行 | ✅ 2026-09-12 |
| 验收纪律 2（跨课不召回 + 同课两份都召回） | ✅ 真库达成，见 §十一 / §十二 |
| **那次部署跑的是哪个构建** | ⚠️ **是当时的 HEAD `a0d1712`，即阶段 4 的旧构建** —— 阶段 5–6 的改动当时还没提交。详见 `docs/F1/F1进度.md` §十二 |
| 用**新构建**重跑一次验收 | ⬜ **未做。** 提交 + 推送 + 重新部署之后，用 `--expect-commit <sha>` 跑 `verify_retrieval_prod.py` |
| 验收纪律 1（P0 vs P3 真调模型） | ⬜ 未跑 |

⚠️ **本文档写完 ≠ 已部署，而「已部署」也 ≠ 「部署的是你以为的那一版」。**
后半句是 2026-09-12 真实发生的事：部署成功、验收前三节全绿，
而线上跑的是一个不含本轮改动的构建。**三件事分开记**（口径 F34 的同一条）——
文档、部署、以及**那次部署在跑哪个提交**。
