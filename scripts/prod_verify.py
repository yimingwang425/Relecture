"""真库/真服务验收脚本的共用件：**唤醒**（冷启动）与 **CA 根证书**。

放在仓库根 `scripts/` 而不是 `api/scripts/`，是因为它的两个调用方分处两边：
`api/scripts/*.py` 与 `supabase/verify_retrieval_prod.py`。
两边各写一份的话，「几次算放弃」就有了两个答案，而两份漂开**不会报错**。

---------------------------------------------------------------- 唤醒

**Render 免费层 15 分钟无请求即休眠，冷启动 30 秒以上。**

## 为什么这件事需要一个共用实现

两个验收脚本（`acceptance_p0_p3.py` 与 `supabase/verify_retrieval_prod.py`）
都会被冷启动打到。各写一份的话，「几次算放弃」就有了两个答案，
而两份漂开**不会报错**——它只会让两份报告里的「失败」含义不同。

## 三条判断，跑之前定死

1. **冷启动超时不是失败。** 把它判红得到的是一条假红，而假红比没有断言更糟：
   它让人去查一个不存在的 bug，下一次真红时又会被当成「又是冷启动吧」。
2. **但重试必须有上限，本模块是 3 次。** 无限重试会把「服务真的挂了」
   变成「脚本一直在转」——拿一个响亮的失败换一个安静的失败。
   这正是 F16 反对的那件事的另一个形态。
3. **唤醒本身不计入绿数。** 它不是一条断言——「服务能被唤醒」不证明
   任何业务行为是对的。计进去的话，一份全部业务断言都红了的报告
   仍然会有一条绿。所以本模块**返回布尔，不调用调用方的 check()**。

## 唤醒失败与断言变红是两种失败，打印必须分开

唤醒失败 → 去看 Render 的 Logs（服务没起来 / 环境变量没配）。
断言变红 → 去看代码。压成一句「验收失败」的话，两条完全不同的排查路径
长得一模一样。

---------------------------------------------------------------- CA 根证书

`ssl_context()` 与 `supabase/verify_rls_prod.py` / `verify_corpus_prod.py` 里那两份
**是同一份处置**：找不到根证书就退出，**不关校验**。
关掉校验的脚本仍然会全绿，而它验的东西已经不是「与真库的 TLS 连接」了——
那是一个永远通过的检查（口径 F16）。

⚠️ **那两份没有改成 import 本模块**，登记在案：它们的价值之一正是
「零依赖、不 import 仓库里任何东西」，而本模块的新增调用方没有那条约束。
**触发条件**：第四处出现，或那两份里的 CA 处置需要改动时——
届时三处一起收到这里，别只改一处。

决策日期：2026-09-12
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["WAKE_ATTEMPTS", "WAKE_GAP_SECONDS", "WAKE_TIMEOUT_SECONDS",
           "wake_service", "ssl_context"]


def ssl_context() -> ssl.SSLContext:
    """找得到 CA 就用，找不到就**退出**——不关校验。

    与 `supabase/verify_rls_prod.py` 开头那份逐条相同（见模块 docstring 的登记）。
    """
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"] > 0:
        return ctx
    for cafile in (os.environ.get("SSL_CERT_FILE"), "/etc/ssl/cert.pem"):
        if cafile and Path(cafile).is_file():
            ctx = ssl.create_default_context(cafile=cafile)
            if ctx.cert_store_stats()["x509_ca"] > 0:
                return ctx
    sys.exit("找不到 CA 根证书。macOS 上跑一次 Install Certificates.command，或设 SSL_CERT_FILE。")

#: 重试次数上限。**3 次，不是无限**（见模块 docstring 第 2 条）。
WAKE_ATTEMPTS = 3
#: 两次之间等多久。Render 冷启动实测 30s+，等 20s 再打让第二发大概率落在启动之后。
WAKE_GAP_SECONDS = 20
#: 单发的超时。比冷启动本身短是刻意的——我们靠重试等，不靠把一发拉长：
#: 拉长单发的话，「服务挂了」与「还在启动」在脚本里长得一模一样，都是一个长时间的卡住。
WAKE_TIMEOUT_SECONDS = 30


def wake_service(base: str, *, attempts: int = WAKE_ATTEMPTS,
                 gap: int = WAKE_GAP_SECONDS, timeout: int = WAKE_TIMEOUT_SECONDS,
                 sleep=time.sleep) -> bool:
    """打 `/health` 把服务叫醒。**返回 True/False，不 exit**（由调用方决定怎么报）。

    `/health` 是唯一不需要 token 的端点，所以用它唤醒不会把
    「服务没醒」与「token 不对」混成同一个失败。
    """
    url = f"{base.rstrip('/')}/health"
    print(f"唤醒 {url} …")
    for i in range(1, attempts + 1):
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, method="GET")
            ctx = ssl_context() if url.startswith("https://") else None
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                body = r.read().decode()
                dt = time.monotonic() - t0
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = body[:120]
                print(f"  第 {i} 次：{r.status}  ← 已唤醒，耗时 {dt:.1f}s   {parsed}")
                return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            dt = time.monotonic() - t0
            reason = getattr(exc, "reason", exc)
            print(f"  第 {i} 次：失败（{reason}，{dt:.1f}s）"
                  + ("  ← 免费层冷启动 30s+，这不是失败" if i < attempts else ""))
            if i < attempts:
                print(f"      等 {gap}s 再试（还剩 {attempts - i} 次）")
                sleep(gap)

    print(f"\n\033[31m唤醒失败\033[0m：{attempts} 次都没打通 {url}。\n"
          "  **这不是某条断言变红，是服务压根没应答。** 去看 Render 的 Logs：\n"
          "  服务起来了吗？环境变量配全了吗（缺一个会启动即失败，这是设计）？\n"
          "  两种失败的排查路径完全不同，所以这里不把它报成「验收失败」。")
    return False
