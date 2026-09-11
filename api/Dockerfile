# 产品服务端（Railway · 新加坡区）。
#
# ⚠️ **构建上下文是仓库根，不是 api/**：本服务用路径注入引同仓库的
# `explain` 与 `a_rules`（见 api/api/__init__.py），它们是兄弟目录。
# Railway 上把 Root Directory 留在 `/`，Dockerfile Path 填 `api/Dockerfile`。
#
#     docker build -f api/Dockerfile -t f1-api .
#
# 为什么是 Railway 而不是 Vercel（2026-09-11 拍板）：
#   ① 原生依赖——F2 摄入要 PyMuPDF + numpy，Vercel 的 Python runtime 打包这类
#      依赖是持续的痛，Railway 就是普通容器；
#   ② 请求时长——生成带最多 2 次重生成，轻松超 60s；
#   ③ 部署地——PRD §13.3 写着应用服务器在新加坡，Railway 有 SG 区；
#   ④ F1方案 §6.2 本来就假设 Railway（阶段 1/1.5 没走是对的：当时多一跳只是把
#      RLS 从数据库执行变成应用转发；**现在不一样了，这次是真的需要跑 Python**）。
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
COPY slicer /srv/slicer
COPY parse-service /srv/parse-service
COPY 实验脚本/A类规则计算脚本 /srv/实验脚本/A类规则计算脚本
COPY f1_samples/synthetic_deck.md /srv/f1_samples/synthetic_deck.md
COPY api /srv/api
RUN pip install --no-cache-dir -e /srv/slicer -e /srv/parse-service

ENV PYTHONUNBUFFERED=1 PYTHONPATH=/srv/api
EXPOSE 8000

# `create_app()` 在导入时 load_settings —— 缺环境变量**在启动时就炸**，
# 不是等到第一个请求。配错了要立刻看得见（同 web/lib/env.ts 的做法）。
CMD ["sh", "-c", "uvicorn api.main:app_from_env --factory --host 0.0.0.0 --port ${PORT:-8000}"]
