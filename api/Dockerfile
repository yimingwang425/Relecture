# 产品服务端（Render · 新加坡区）。
#
# ⚠️ **构建上下文是仓库根，不是 api/**：本服务用路径注入引同仓库的
# `explain` 与 `a_rules`（见 api/api/__init__.py），它们是兄弟目录。
# Render 上 **Root Directory 留空**（= 仓库根），**Dockerfile Path 填 `api/Dockerfile`**。
# 填法与免费层的休眠行为见 `docs/F3/部署_Render.md`。
#
#     docker build -f api/Dockerfile -t f1-api .
#
# 为什么不是 Vercel（2026-09-11 拍板，**四条技术理由原样保留**）：
#   ① 原生依赖——F2 摄入要 PyMuPDF + numpy，Vercel 的 Python runtime 打包这类
#      依赖是持续的痛，容器平台就是普通容器；
#   ② 请求时长——生成带最多 2 次重生成，轻松超 60s；
#   ③ 部署地——PRD §13.3 写着应用服务器在新加坡，本平台有 SG 区；
#   ④ F1方案 §6.2 本来就假设有这么一跳（阶段 1/1.5 没走是对的：当时多一跳只是把
#      RLS 从数据库执行变成应用转发；**现在不一样了，这次是真的需要跑 Python**）。
#
# 为什么从 Railway 换成 Render（2026-09-12）：
#   **理由是账号级的，不是技术的。** Railway 的免费层已被另一个项目占用，
#   再开一个 project 要付费。四条技术理由对 Render **逐条同样成立**：
#   普通容器（①）、无 60s 请求上限（②）、有新加坡区 Singapore（③）、
#   需要跑 Python 这件事与平台无关（④）。
#   所以换平台**不推翻任何一条已有论证**，只是换了一个满足它们的执行者。
#
# ⚠️ **Render 免费层有一条 Railway 没有的行为：15 分钟无请求即休眠，
#    冷启动 30 秒以上。** 它会让验收脚本的第一发请求超时，
#    而那**不是失败**。处置写在 `api/scripts/acceptance_p0_p3.py`
#    与 `supabase/verify_retrieval_prod.py` 的唤醒逻辑里：先打 `/health` 唤醒并重试，
#    **重试三次仍失败才判失败**——不无限重试（无限重试会把「服务真的挂了」
#    变成「脚本一直在转」，那是把一个响亮的失败换成一个安静的失败）。
FROM python:3.13-slim

WORKDIR /srv

# 先装依赖再拷源码：源码一改不必重装依赖。
COPY api/pyproject.toml /srv/api/pyproject.toml
# numpy 与 openai 是 retrieval 的依赖（组文本与嵌入）；pymupdf 是解析。
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic httpx \
    "pyjwt[crypto]" anthropic numpy openai pymupdf python-multipart

# 兄弟包。目录名含中文是既有事实（`实验脚本/A类规则计算脚本`），
# COPY 走 UTF-8 路径没问题；改名是另一件事，不夹在这次接线里。
COPY explain /srv/explain
COPY retrieval/retrieval /srv/retrieval/retrieval
COPY 实验脚本/A类规则计算脚本 /srv/实验脚本/A类规则计算脚本
COPY f1_samples/synthetic_deck.md /srv/f1_samples/synthetic_deck.md
COPY api /srv/api

# ⚠️ **pip 装的那两个包必须落在 /srv 之外。** 第一次构建这个镜像时抓到的真 bug：
#
#   原本是 `COPY slicer /srv/slicer` + `pip install -e /srv/slicer`，
#   运行时 `from slicer import slice_document` 报
#   `ImportError: cannot import name 'slice_document' from 'slicer' (unknown location)`。
#
#   机制：uvicorn 把 cwd（`/srv`）放进 `sys.path` 的**第一位**，而 `/srv/slicer`
#   是**项目目录**（真包在 `/srv/slicer/slicer`），它没有 `__init__.py`
#   → PathFinder 把它当成一个**命名空间包**并直接返回。
#   setuptools 的 editable finder 是 `sys.meta_path.append` 进去的，**排在
#   PathFinder 之后**，于是根本轮不到它。`unknown location` 就是命名空间包的签名。
#
#   为什么本机跑不出来：本机的 cwd 是 `api/`，`/srv` 那一位上没有同名目录。
#   **这个 bug 只有真构建 + 真启动才会出现**——这正是「先本地构建一次再交付」
#   要买的东西（Render 的构建日志是最难查错的地方）。
#
# `explain` / `retrieval` / `a_rules` 不受影响：它们走 `api/api/__init__.py` 的
# 路径注入，`sys.path.insert(0, ...)` 插在 cwd **前面**，真包先被找到。
COPY slicer /opt/pkgs/slicer
COPY parse-service /opt/pkgs/parse-service
RUN pip install --no-cache-dir -e /opt/pkgs/slicer -e /opt/pkgs/parse-service

# 构建标识（口径 F17）。Render 会自动注入 `RENDER_GIT_COMMIT`，所以线上不用传；
# 本机构建时传一次，验收脚本才问得出「我在跟哪个构建说话」：
#
#     docker build -f api/Dockerfile --build-arg BUILD_COMMIT=$(git rev-parse HEAD) -t f1-api .
#
# **不在镜像里跑 `git rev-parse`**：镜像里没有 `.git`（也不该有——那会把整个历史
# 塞进镜像）。标识必须由构建那一刻的外部信息给，镜像自己推不出来，
# 而这正是它可信的原因。不传时 `/health` 的 `build_commit` 是 **null**，
# 不是 'unknown' —— 见 api/api/build.py。
ARG BUILD_COMMIT=""
ENV BUILD_COMMIT=${BUILD_COMMIT}

ENV PYTHONUNBUFFERED=1 PYTHONPATH=/srv/api
EXPOSE 8000

# `create_app()` 在导入时 load_settings —— 缺环境变量**在启动时就炸**，
# 不是等到第一个请求。配错了要立刻看得见（同 web/lib/env.ts 的做法）。
CMD ["sh", "-c", "uvicorn api.main:app_from_env --factory --host 0.0.0.0 --port ${PORT:-8000}"]
