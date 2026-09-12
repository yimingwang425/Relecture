"""**这个服务在跑哪个构建。**

## 它是被一次真实的误读逼出来的（2026-09-12）

Render 第一次部署之后，`verify_retrieval_prod.py` 的第 4 节报了两件事：
脚本崩在 `KeyError: 'retrieval'`，而 `/explain` 的正文在讲**布隆过滤器**——
那是 `f1_samples/synthetic_deck.md`，阶段 3 的替身语料。

真实原因：**Render 建的是 `a0d1712`（当时的 HEAD），而阶段 5–6 的全部改动
都还没提交。** 那个构建里 `ExplainRequest` 没有 `course_id`，
pydantic 的 `extra` 默认是 `ignore`，于是**那个字段被静默丢掉**、
检索走替身、返回 200。

**8 条断言在一个旧构建上通过了**，因为没有任何一条问过「你是哪个构建」。

口径 **F17** 早就写着「验收前先核对读的是哪个构建（`x-build-commit`）」——
web 侧有，**这个服务一个都没有**。F17 在这里等于没落地。

## 两条出口，与 web 侧同形

1. `GET /health` 的 `build_commit`
2. **每个响应的 `x-build-commit` 头**（web 侧就是这个头，同名同义）

## 取不到时报 `null`，**不报一个看起来正常的值**

没有构建标识与「构建标识是 `unknown`」在字符串上都是「有个值」，
但对验收的含义相反：前者是「这个服务答不出自己是谁」，
后者会被读成「它答了，只是内容是 unknown」。所以取不到就是 JSON `null`，
而验收脚本对 `null` 的处置是**拒绝解释结果**，不是继续跑。

## 环境变量从哪来

| 平台 | 变量 | 谁设的 |
| --- | --- | --- |
| Render | `RENDER_GIT_COMMIT` | 平台自动注入 |
| 本机 / 其他 | `BUILD_COMMIT` | `docker build --build-arg` 或 `docker run -e` |

**不去跑 `git rev-parse`**：镜像里没有 `.git`（Dockerfile 一个 `COPY .git` 都没有，
而且加上它会把整个历史塞进镜像）。构建标识必须由**构建那一刻**的外部信息给，
镜像自己推不出来——这一点正是它可信的原因。

决策日期：2026-09-12
"""

from __future__ import annotations

import os

__all__ = ["BUILD_COMMIT_ENV_VARS", "build_commit"]

#: 按顺序试。Render 自动注入第一个；第二个给本机与其他平台。
BUILD_COMMIT_ENV_VARS = ("RENDER_GIT_COMMIT", "BUILD_COMMIT")


def build_commit(env: dict[str, str] | None = None) -> str | None:
    """这个进程在跑哪个提交。**取不到返回 None，不返回 'unknown'。**

    见模块 docstring：`None` 和 `'unknown'` 在验收里的含义是相反的。
    """
    e = os.environ if env is None else env
    for name in BUILD_COMMIT_ENV_VARS:
        v = (e.get(name) or "").strip()
        if v:
            return v
    return None
