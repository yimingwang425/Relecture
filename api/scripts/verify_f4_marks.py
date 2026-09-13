#!/usr/bin/env python3
"""F4 · **真库验收**：`confusion_marks` 的迁移、RLS、幂等、交集语义、cascade。

    cd api && .venv/bin/python scripts/verify_f4_marks.py \
        --base https://<service>.onrender.com \
        --expect-commit $(git rev-parse HEAD)

前置：**先在 Supabase SQL Editor 跑 `20260913150000_f4_confusion_marks.sql`。**
反了的话标记会写不进去而**接口照常返回 200**（埋点失败不打断主链路，口径 F42）——
这个错不会以「服务挂了」的样子出现。第 1 节就是为了让这件事有个明确答案。

## 它验的四件事，各自为什么在这里而不在别处

| | 为什么必须在真库上 |
| --- | --- |
| 迁移应用了没有 | 本地跑的是文件，真库跑的是**有没有人去执行那个文件**（口径 F34） |
| 跨用户隔离 | 本地那条在 PGlite 上模拟 `auth.uid()`；真库上是 Supabase 真的签发的 JWT |
| **`ov.` 到底是不是 `&&`** | **只有真库能答。** 本地断言里的交集是我用 Python 写的 |
| 幂等 | 本地验的是唯一约束；这里验**服务端把 23505 翻成 200** 这一整跳 |

第三条是本轮最容易死得无声无息的一条：`ov.` 写错的表现是永远 0 行 =
「功能好像没生效」，而没有任何东西会红。所以它用**有交集**与**无交集**
两组键各查一次——两条一起才排除「恒空」与「恒满」。

## ⚠️ 它会在验收账号名下留下几行 marks，删不掉

`confusion_marks` **不授 delete**（口径 F60：v1 不做取消标记），
所以本脚本写进去的行**清不掉**。用 `F1_VERIFY_EMAIL_A/B` 那两个专用账号，
别用你自己的。行数会随每次跑增加，这是那条口径的直接代价，如实登记。

## ⚠️ cascade 那一节默认**不跑**，而且不静默跳过

「删账号 → marks 消失」在真库上要两样服务端没有也不该有的东西：
删用户要 admin API，删完之后再数行数要 service_role 读（RLS 让谁都看不见
一个已注销用户的行）。本项目**不持有 service_role key**（口径 F40），
三个验收脚本开头都明确拒绝它。

所以默认打印一行**未观测**（不是绿、不是红），并指出它在哪里被验过：
`supabase/verify_rls_local.mjs` 的「注销即清空」一节 + 反向控制 R17
（把外键改成 no action，删号当场被 23503 挡住 —— 证明那个「1 → 0」
确实是 cascade 干的）。要在真库上也观测一次，给 `--admin-key`：
那时它**只**用于「建一个一次性账号 / 删掉它 / 读它的行数」，
其余每一条断言仍然走各自用户自己的 JWT。

决策日期：2026-09-13
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "api"))
sys.path.insert(0, str(REPO / "scripts"))

import api  # noqa: F401,E402  —— 注入 explain / retrieval 路径
from prod_verify import ssl_context, wake_service  # noqa: E402

from api.marks import slice_key  # noqa: E402
from api.supabase import SupabaseClient, SupabaseError  # noqa: E402

SSL_CTX = ssl_context()


# ---------------------------------------------------------------- 环境


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for dotenv in (REPO / "supabase" / ".env", REPO / "api" / ".env"):
        if dotenv.is_file():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY",
                           "F1_VERIFY_PASSWORD") if not env.get(k)]
    if missing:
        sys.exit(f"缺少环境变量：{', '.join(missing)}（见 api/.env.example）")
    key = env["SUPABASE_PUBLISHABLE_KEY"]
    if key.startswith("sb_secret_") or "service_role" in key:
        sys.exit("拒绝运行：给的是 secret / service_role key。"
                 "用它跑的 RLS 验收什么也证明不了（口径 F40）。")
    return env


ENV = load_env()
URL = ENV["SUPABASE_URL"].rstrip("/")
APIKEY = ENV["SUPABASE_PUBLISHABLE_KEY"]
PASSWORD = ENV["F1_VERIFY_PASSWORD"]
EMAIL_A = ENV.get("F1_VERIFY_EMAIL_A", "f1-rls-a@example.com")
EMAIL_B = ENV.get("F1_VERIFY_EMAIL_B", "f1-rls-b@example.com")


def call(method: str, url: str, *, token: str | None = None, apikey: str | None = None,
         payload: object = None, timeout: int = 120) -> tuple[int, object, str]:
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if apikey:
        req.add_header("apikey", apikey)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        ctx = SSL_CTX if url.startswith("https://") else None
        with urllib.request.urlopen(req, data, timeout=timeout, context=ctx) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw), raw
        except json.JSONDecodeError:
            return e.code, None, raw


def sign_in(email: str) -> tuple[str, str]:
    s, b, raw = call("POST", f"{URL}/auth/v1/token?grant_type=password",
                     apikey=APIKEY, payload={"email": email, "password": PASSWORD})
    if s != 200 or not isinstance(b, dict):
        sys.exit(f"账号 {email} 登录失败（{s}）：{raw[:300]}")
    return b["access_token"], b["user"]["id"]


# ---------------------------------------------------------------- 断言


PASSED = FAILED = 0
LAYERS = {"迁移": "\033[33m[迁移]\033[0m", "RLS": "\033[35m[RLS]\033[0m",
          "交集": "\033[36m[交集]\033[0m", "幂等": "\033[34m[幂等]\033[0m",
          "服务": "\033[32m[服务层]\033[0m", "cascade": "\033[31m[cascade]\033[0m"}


def check(layer: str, name: str, cond: bool, detail: str = "") -> bool:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✗\033[0m"
    print(f"  {mark} {LAYERS[layer]} {name}" + (f"   {detail}" if detail else ""))
    return cond


def unobserved(name: str, why: str) -> None:
    """**未观测**：既不是绿也不是红，不计入任何一边。

    计进绿数就是「跳过了却退出 0」（同 F15 对 `--allow-offline` 的处置）；
    计进红数则是一条假红，它会让人去查一个不存在的 bug。
    """
    print(f"  \033[2m·\033[0m \033[33m[未观测]\033[0m {name}\n      \033[2m{why}\033[0m")


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="服务端地址（Render 或本机容器）")
    ap.add_argument("--expect-commit", default=None,
                    help="期望线上跑的是哪个提交（口径 F17）。给了就断言它相等；"
                         "不给只打印。**不给的时候请自己核对那一行**")
    ap.add_argument("--admin-key", default=None,
                    help="仅用于 cascade 那一节（建一次性账号 / 删它 / 数它的行）。"
                         "不给就打印「未观测」，不静默跳过")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    if args.admin_key and args.admin_key == APIKEY:
        sys.exit("--admin-key 与 publishable key 相同。那把钥匙删不掉账号，"
                 "而这一节会以「cascade 没生效」的样子失败。")

    print(f"\n生产项目：{URL}\n服务端：{base}")
    print("\033[2m⚠️ 本脚本会在验收账号名下留下几行 marks，且**删不掉**"
          "（confusion_marks 不授 delete，口径 F60）。\033[0m\n")

    if not wake_service(base):
        return 1

    # ================================================================ 0 · 构建标识
    #
    # 口径 F17：验收前先核对读的是哪个构建。2026-09-12 因为没有这一节，
    # 8 条断言在一个旧构建上全绿过。
    print("\n\033[1m0 · 构建标识（口径 F17：先核对读的是哪个构建）\033[0m")
    s, hb, _ = call("GET", f"{base}/health")
    deployed = (hb or {}).get("build_commit") if isinstance(hb, dict) else None
    if deployed:
        print(f"  线上构建：\033[1m{deployed}\033[0m")
    else:
        print("  \033[33m线上构建：答不出（/health 的 build_commit 是 null）。"
              "\n    没有它，下面每一条绿都说不出是哪一版的绿。\033[0m")
    if args.expect_commit:
        check("服务", "线上构建 == --expect-commit",
              deployed is not None and deployed.startswith(args.expect_commit[:7]),
              f"线上 {deployed!r} / 期望 {args.expect_commit!r}  "
              "← 不等就别解释下面任何一条结果（口径 F17）")

    tok_a, uid_a = sign_in(EMAIL_A)
    tok_b, uid_b = sign_in(EMAIL_B)
    if uid_a == uid_b:
        sys.exit("A 与 B 是同一个用户——跨用户那几条会平凡通过。换邮箱。")

    #: **生产实现本身**，不是脚本里另写一份 PostgREST 客户端。
    #: 另写一份的话，`ov.` 这条断言验的是脚本的拼法，而不是服务真的发出去的那一份。
    db = SupabaseClient(f"{URL}/rest/v1", APIKEY)

    # ================================================================ 1 · 迁移应用了没有
    #
    # 表 / 索引 / RLS 策略 / 外键的 cascade 属性 —— **各查一次，不合成一句**。
    # 压成「迁移已应用」的话，一个只建了表没建策略的库同样会绿，
    # 而那个库上跨用户读**全部返回 0 行**（RLS 开着但没有 select 策略），
    # 读起来像「隔离得很好」。
    print("\n\033[1m1 · 迁移应用了没有（表 / 索引 / 策略 / 外键，各查一次）\033[0m")
    s, shape, raw = call("POST", f"{URL}/rest/v1/rpc/f4_confusion_marks_shape",
                         token=tok_a, apikey=APIKEY, payload={})
    if s != 200 or not isinstance(shape, dict):
        check("迁移", "自述函数 f4_confusion_marks_shape 可调用", False,
              f"HTTP {s} {raw[:200]}  ← 迁移多半还没跑。**先跑迁移再往下看**，"
              "下面每一条的失败都会是它的回声")
        print(f"\n\033[1m合计\033[0m  绿 {PASSED} · 红 {FAILED}\n")
        return 1
    check("迁移", "自述函数可调用", True, "← 它本身就是「这个迁移跑过了」的第一条证据")
    check("迁移", "confusion_marks 表在，且 RLS 开着",
          shape.get("table_exists") is True and shape.get("rls_enabled") is True,
          json.dumps({k: shape.get(k) for k in ("table_exists", "rls_enabled")}))
    check("迁移", "slice_ids 是 text[]（口径 F66：切片身份是两段，不是 uuid）",
          (shape.get("columns") or {}).get("slice_ids") == "ARRAY",
          f"{(shape.get('columns') or {}).get('slice_ids')!r}")
    check("迁移", "GIN 索引在（`&&` 要用）",
          (shape.get("indexes") or {}).get("confusion_marks_slice_ids_gin") == "gin",
          json.dumps(shape.get("indexes"), ensure_ascii=False))
    pol = shape.get("policies") or {}
    check("迁移", "两条策略：select + insert，且**没有**别的",
          pol.get("confusion_marks_select_own") == "r"
          and pol.get("confusion_marks_insert_own") == "a"
          and len(pol) == 2,
          json.dumps(pol))
    check("迁移", "authenticated 只有 SELECT + INSERT（口径 F60：不做取消标记）",
          sorted(shape.get("grants") or []) == ["INSERT", "SELECT"],
          json.dumps(shape.get("grants")))
    check("迁移", "user_id 外键是 on delete cascade（PRD §12.1「注销即清空」）",
          shape.get("user_fk_delete_rule") == "c",
          f"{shape.get('user_fk_delete_rule')!r}  ← 'c' = cascade；'a' = no action")
    cons = shape.get("constraints") or {}
    check("迁移", "唯一约束 + 空 slice_ids 的 CHECK 都在",
          cons.get("confusion_marks_user_round_uniq") == "u"
          and cons.get("confusion_marks_slice_ids_nonempty") == "c",
          json.dumps(cons))
    check("迁移", "12 个 event_type + slice_ids 非空护栏都在位",
          shape.get("event_types_known") == 12 and shape.get("slice_ids_check") == 1,
          f"types={shape.get('event_types_known')} check={shape.get('slice_ids_check')}"
          "  ← types=11 说明那次 create or replace 漏了 confusion_mark_created")

    # ================================================================ 2 · 服务层：写一条
    #
    # 走 `POST /api/v1/marks`，**不是直接往表里写**：要验的正是那一整跳
    # （鉴权 → RLS → 幂等翻译 → 计数 → 埋点）。
    print("\n\033[1m2 · 服务层写入（POST /api/v1/marks）\033[0m")
    round_a = str(uuid.uuid4())
    doc = str(uuid.uuid4())
    keys = [slice_key(doc, "s001"), slice_key(doc, "s002")]
    #: **同一个 slice_id、不同的 document** —— 用它验「键是两段」（口径 F66）。
    other_doc_key = slice_key(str(uuid.uuid4()), "s001")

    before = db.count_marks(tok_a)
    s, out, raw = call("POST", f"{base}/api/v1/marks", token=tok_a, payload={
        "round_id": round_a, "slice_ids": keys, "question": "F4 真库验收：这一段没看懂",
        "session_id": str(uuid.uuid4())})
    ok_write = check("服务", "第一次提交 → 200 且 created=true",
                     s == 200 and isinstance(out, dict) and out.get("created") is True,
                     f"HTTP {s} {raw[:200]}")
    if ok_write:
        check("服务", "mark_count_after 比提交前多 1",
              out.get("mark_count_after") == before + 1,
              f"{before} → {out.get('mark_count_after')}")
        ev = out.get("events") or {}
        check("服务", "confusion_mark_created 真的写进事件表了",
              ev.get("emitted") == 1 and ev.get("failed") == 0,
              json.dumps(ev, ensure_ascii=False)
              + "  ← failed>0 且带 23514，多半是迁移里那次 replace 没跑")

    check("服务", "空 slice_ids → 422（一条空 mark 是死数据）",
          call("POST", f"{base}/api/v1/marks", token=tok_a, payload={
              "round_id": str(uuid.uuid4()), "slice_ids": [], "question": "x"})[0] == 422)
    check("服务", "多发一个未知字段 → 422，不静默丢掉（口径 F52）",
          call("POST", f"{base}/api/v1/marks", token=tok_a, payload={
              "round_id": str(uuid.uuid4()), "slice_ids": keys, "question": "x",
              "concept": "布隆过滤器"})[0] == 422)
    check("服务", "没有 token → 401",
          call("POST", f"{base}/api/v1/marks", payload={
              "round_id": str(uuid.uuid4()), "slice_ids": keys, "question": "x"})[0] == 401)

    # ================================================================ 3 · 幂等
    print("\n\033[1m3 · 幂等（同一 round_id 提交两次）\033[0m")
    s2, out2, raw2 = call("POST", f"{base}/api/v1/marks", token=tok_a, payload={
        "round_id": round_a, "slice_ids": keys, "question": "F4 真库验收：这一段没看懂",
        "session_id": str(uuid.uuid4())})
    check("幂等", "第二次提交 → 200（不是 409），created=false",
          s2 == 200 and isinstance(out2, dict) and out2.get("created") is False,
          f"HTTP {s2} {raw2[:200]}  ← 409 会逼前端多一个它不关心的分支")
    same_round = [m for m in db._get(  # noqa: SLF001 —— 验收脚本刻意看原始行
        f"/confusion_marks?select=id&round_id=eq.{round_a}", tok_a)]
    check("幂等", "表里仍然只有一行", len(same_round) == 1, f"{len(same_round)} 行")
    check("幂等", "mark_count_after 没有涨（它是「之后」的数）",
          isinstance(out2, dict) and out2.get("mark_count_after") == before + 1,
          f"{out2.get('mark_count_after') if isinstance(out2, dict) else out2}")

    # 允许控制：**不同**的 round 必须各存一行。
    # 少了它，一个「第二次一律吞掉」的实现也能让上面全绿。
    round_b = str(uuid.uuid4())
    s3, out3, _ = call("POST", f"{base}/api/v1/marks", token=tok_a, payload={
        "round_id": round_b, "slice_ids": [other_doc_key],
        "question": "另一轮", "session_id": str(uuid.uuid4())})
    check("幂等", "允许控制：另一个 round_id 存得进去",
          s3 == 200 and isinstance(out3, dict) and out3.get("created") is True
          and out3.get("mark_count_after") == before + 2,
          f"HTTP {s3} count={out3.get('mark_count_after') if isinstance(out3, dict) else None}")

    # ================================================================ 4 · 交集语义
    #
    # ⚠️ **本轮唯一只有真库能答的一节。** 本地断言里的 `&&` 是我用 Python 写的，
    # 它按我以为的语义实现；`ov.` 到底是不是 `&&`，只有这里能验。
    # 写错的表现是永远 0 行 =「功能好像没生效」，而没有任何东西会红。
    print("\n\033[1m4 · 交集语义（PostgREST 的 ov. 到底是不是 &&）\033[0m")
    hit = db.marks_overlapping(tok_a, [keys[1]])
    check("交集", "有交集 → 查得到那一轮",
          any(m.get("round_id") == round_a for m in hit),
          f"{len(hit)} 行 ← 0 行说明 ov. 这条查询压根没匹配上任何东西")

    miss = db.marks_overlapping(tok_a, [slice_key(str(uuid.uuid4()), "s999")])
    check("交集", "无交集 → 0 行（**不是恒满**）", len(miss) == 0, f"{len(miss)} 行")

    # 口径 F66：同一个 `slice_id` 挂在另一份讲义下，**不许命中**。
    # 只拿 slice_id 当键的话这一条会命中，而那就是恒触发的注入。
    cross_doc = db.marks_overlapping(tok_a, [slice_key(str(uuid.uuid4()), "s001")])
    check("交集", "另一份讲义的同名 s001 不命中（口径 F66：键是两段）",
          all(m.get("round_id") != round_a for m in cross_doc),
          f"{len(cross_doc)} 行 ← 命中了就说明键退化成了 slice_id 一段")

    # ================================================================ 5 · 跨用户隔离
    print("\n\033[1m5 · 跨用户隔离（RLS）\033[0m")
    b_sees = db._get(  # noqa: SLF001
        f"/confusion_marks?select=id&round_id=eq.{round_a}", tok_b)
    check("RLS", "B 拿自己的 token 查 A 的那一轮 → 0 行", len(b_sees) == 0,
          f"{len(b_sees)} 行")
    b_overlap = db.marks_overlapping(tok_b, keys)
    check("RLS", "B 的交集查询也看不见 A 的 mark → 0 行", len(b_overlap) == 0,
          f"{len(b_overlap)} 行 ← 交集查询走的是同一套策略，不是另一条路")

    # **允许控制**：B 得能读到自己的行，否则「0 行」也可能是整张表读不了。
    round_b_own = str(uuid.uuid4())
    call("POST", f"{base}/api/v1/marks", token=tok_b, payload={
        "round_id": round_b_own, "slice_ids": [slice_key(str(uuid.uuid4()), "s001")],
        "question": "B 自己的一条", "session_id": str(uuid.uuid4())})
    b_own = db._get(  # noqa: SLF001
        f"/confusion_marks?select=id&round_id=eq.{round_b_own}", tok_b)
    check("RLS", "允许控制：B 读得到自己的那一行", len(b_own) == 1,
          f"{len(b_own)} 行 ← 少了它，上面两条「0 行」也可能是整张表读不了")

    # 冒名写入：A 拿自己的 token 直接往表里写一行 user_id = B。
    # ⚠️ 这一条**绕开服务端直打 PostgREST**——服务端本来就填自己的 user_id，
    # 经它走的话测的是服务端的代码，不是 with check。
    try:
        db._post(  # noqa: SLF001
            "/confusion_marks", tok_a,
            {"user_id": uid_b, "round_id": str(uuid.uuid4()),
             "slice_ids": [slice_key(str(uuid.uuid4()), "s001")], "question": "冒名"})
        check("RLS", "A 以 B 的名义直写 PostgREST → 被拒", False, "竟然成功了")
    except SupabaseError as exc:
        check("RLS", "A 以 B 的名义直写 PostgREST → 被拒", "42501" in exc.message,
              exc.message[:160])

    # 不授 delete（口径 F60）。
    s_del, _, raw_del = call(
        "DELETE", f"{URL}/rest/v1/confusion_marks?round_id=eq.{round_a}",
        token=tok_a, apikey=APIKEY)
    still = db._get(f"/confusion_marks?select=id&round_id=eq.{round_a}", tok_a)  # noqa: SLF001
    check("RLS", "A 删不掉自己的 mark（v1 不做取消标记）", len(still) == 1,
          f"delete HTTP {s_del}，行仍在 ← 这也是本脚本留下的行清不掉的原因")

    # ================================================================ 6 · cascade
    print("\n\033[1m6 · 注销即清空（on delete cascade）\033[0m")
    if not args.admin_key:
        unobserved(
            "删账号 → marks 消失",
            "要 admin API 删用户 + service_role 读已注销用户的行，而本项目不持有"
            " service_role key（口径 F40）。第 1 节已确认外键的删除规则是 'c'；"
            "**行为**那一层在 supabase/verify_rls_local.mjs 的「注销即清空」一节 +"
            " 反向控制 R17（改成 no action 后删号当场 23503）。"
            "要在真库上也观测一次：--admin-key <service_role key>")
    else:
        rc = _cascade_section(base, args.admin_key)
        if rc:
            return rc

    print(f"\n\033[1m合计\033[0m  绿 {PASSED} · 红 {FAILED}")
    print("\033[2m这批数字与 supabase/verify_rls_local.mjs 的本地绿数**不相加**——"
          "一个是真库观测，一个是本地机制验证（口径 F34）。\033[0m")
    print("\033[2m注入真的进了 prompt 这件事**不在本脚本里**：它要真调模型。"
          "判据是 /explain 响应里的 confusion.injected 与 matched_slice_keys"
          "（机械可查，不靠人读那段讲解）。\033[0m\n")
    return 0 if FAILED == 0 else 1


def _cascade_section(base: str, admin_key: str) -> int:
    """用一个**一次性账号**观测 cascade。admin key 只在这一节出现。

    ⚠️ **先记数再删。** 不先记的话「删完 0 条」分不清是 cascade 生效
    还是本来就没有 —— 那两件事在删完之后长得一模一样。
    """
    email = f"f4-cascade-{uuid.uuid4().hex[:10]}@example.com"
    password = f"Px{uuid.uuid4().hex}!"

    s, made, raw = call("POST", f"{URL}/auth/v1/admin/users", token=admin_key,
                        apikey=admin_key,
                        payload={"email": email, "password": password,
                                 "email_confirm": True})
    if s not in (200, 201) or not isinstance(made, dict):
        check("cascade", "建一次性账号", False, f"HTTP {s} {raw[:200]}")
        return 0
    uid = made["id"]
    print(f"  \033[2m一次性账号 {email} · {uid[:8]}\033[0m")

    s, b, raw = call("POST", f"{URL}/auth/v1/token?grant_type=password",
                     apikey=APIKEY, payload={"email": email, "password": password})
    if s != 200 or not isinstance(b, dict):
        check("cascade", "一次性账号登录", False, f"HTTP {s} {raw[:200]}")
        return 0
    tok = b["access_token"]

    s, _, raw = call("POST", f"{base}/api/v1/marks", token=tok, payload={
        "round_id": str(uuid.uuid4()),
        "slice_ids": [slice_key(str(uuid.uuid4()), "s001")],
        "question": "cascade 验收", "session_id": str(uuid.uuid4())})
    check("cascade", "一次性账号写得进一条 mark", s == 200, f"HTTP {s} {raw[:160]}")

    def admin_count() -> int:
        _, rows, _ = call("GET",
                          f"{URL}/rest/v1/confusion_marks?select=id&user_id=eq.{uid}",
                          token=admin_key, apikey=admin_key)
        return len(rows) if isinstance(rows, list) else -1

    before = admin_count()
    check("cascade", "删号之前先记数：确实有行", before == 1,
          f"{before} 条 ← 这是下一条的前提；不先记的话「0 条」什么也说明不了")

    s, _, raw = call("DELETE", f"{URL}/auth/v1/admin/users/{uid}",
                     token=admin_key, apikey=admin_key)
    check("cascade", "删掉 auth.users 那一行", s in (200, 204), f"HTTP {s} {raw[:160]}")

    after = admin_count()
    check("cascade", "marks 跟着没了", after == 0, f"{before} → {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
