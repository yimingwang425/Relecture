#!/usr/bin/env python3
"""F2 语料与向量 · **生产环境**验收。

    cd supabase && cp .env.example .env && $EDITOR .env
    python3 verify_corpus_prod.py

**只需要 Supabase 连接串 + 一个测试账号**（publishable key + 邮箱密码），
零第三方依赖（urllib），不依赖本机任何 Python 包、venv 或仓库以外的东西。

## 为什么这份脚本必须存在

`20260911100100_f2_embeddings.sql` 的**每一条行为在本地都没验过**：
`verify_rls_local.mjs` 跑的 PGlite 里没有 pgvector 扩展，所以它显式跳过那个文件。
本地绿数与这里的条数**分开报，不合计**——两种证据不得互相顶替（同口径 F34）。

## 每条断言标着它验的是哪一层

失败时要能一眼看出是**传参形状**、**RLS**、还是**排序语义**——三者的处置完全不同：
传参形状错要改函数签名（`vector` → `text` 或 `float[]`）；RLS 错要改策略；
排序语义错说明 `<=>` 用法或向量归一化有问题。压成「检索失败」的话，
三种原因都长一个样。

## 第 1 步是那一发 RPC，不是清单末尾的一句话

`vector(1024)` 经 PostgREST 的 JSON 传参是否可行，**是推理不是观测**
（PostgREST 从文本转换，而 JSON 数组序列化出来的 `[0.1,0.2]` 正是 pgvector 的
输入格式——**看起来**成立）。放第一步，是因为它错了的话后面全部白跑，
而且要改的是函数签名，那属于 schema 改动。

决策日期：2026-09-12
"""

from __future__ import annotations

import json
import math
import os
import ssl
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# 与 verify_rls_prod.py 同一份 CA 处置：找不到根证书就退出，**不关校验**。
def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"] > 0:
        return ctx
    for cafile in (os.environ.get("SSL_CERT_FILE"), "/etc/ssl/cert.pem"):
        if cafile and Path(cafile).is_file():
            ctx = ssl.create_default_context(cafile=cafile)
            if ctx.cert_store_stats()["x509_ca"] > 0:
                return ctx
    sys.exit("找不到 CA 根证书。macOS 上跑一次 Install Certificates.command，或设 SSL_CERT_FILE。")


SSL_CTX = ssl_context()


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    dotenv = HERE / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "F1_VERIFY_PASSWORD")
               if not env.get(k)]
    if missing:
        sys.exit(f"缺少环境变量：{', '.join(missing)}（见 .env.example）")
    key = env["SUPABASE_PUBLISHABLE_KEY"]
    if key.startswith("sb_secret_") or "service_role" in key:
        sys.exit("拒绝运行：给的是 secret / service_role key。用它跑的 RLS 验收什么也证明不了。")
    return env


ENV = load_env()
URL = ENV["SUPABASE_URL"].rstrip("/")
APIKEY = ENV["SUPABASE_PUBLISHABLE_KEY"]
PASSWORD = ENV["F1_VERIFY_PASSWORD"]
EMAIL_A = ENV.get("F1_VERIFY_EMAIL_A", "f1-rls-a@example.com")
EMAIL_B = ENV.get("F1_VERIFY_EMAIL_B", "f1-rls-b@example.com")

MODEL = "text-embedding-3-large@1024"
RULESET = "embed-text-1.0"
DIM = 1024


def call(method: str, path: str, token: str | None = None, payload: Any = None,
         headers: dict[str, str] | None = None) -> tuple[int, Any, str]:
    req = urllib.request.Request(f"{URL}{path}", method=method)
    req.add_header("apikey", APIKEY)
    req.add_header("Authorization", f"Bearer {token or APIKEY}")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=30, context=SSL_CTX) as r:
            raw = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    try:
        return status, (json.loads(raw) if raw else None), raw
    except json.JSONDecodeError:
        return status, None, raw


def sign_in(email: str) -> tuple[str, str]:
    s, b, raw = call("POST", "/auth/v1/token?grant_type=password",
                     payload={"email": email, "password": PASSWORD})
    if s != 200 or not isinstance(b, dict) or "access_token" not in b:
        sys.exit(f"账号 {email} 登录失败（{s}）：{raw[:300]}\n"
                 f"先跑一次 verify_rls_prod.py 把账号建出来，或用 scripts/invite_user.mjs。")
    return b["access_token"], b["user"]["id"]


# ---------------------------------------------------------------- 断言

PASSED = FAILED = 0
LAYERS = {"传参": "\033[36m[传参形状]\033[0m", "RLS": "\033[35m[RLS]\033[0m",
          "排序": "\033[33m[排序语义]\033[0m", "结构": "\033[34m[表结构]\033[0m",
          "清理": "\033[2m[清理]\033[0m"}


def check(layer: str, name: str, cond: bool, detail: str = "") -> bool:
    global PASSED, FAILED
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✗\033[0m"
    if cond:
        PASSED += 1
    else:
        FAILED += 1
    print(f"  {mark} {LAYERS[layer]} {name}" + (f"   {detail}" if detail else ""))
    return cond


def unit(vec: list[float]) -> list[float]:
    """L2 归一化。`1 - (a <=> b)` 是余弦相似度，与向量长度无关，
    但归一化让期望值好算：两个单位向量的点积就是 cos。"""
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def basis(i: int, tilt: float = 0.0) -> list[float]:
    """第 i 维为 1、可选地往第 0 维倾斜一点。**确定性**，两次跑出同一组数。"""
    v = [0.0] * DIM
    v[i] = 1.0
    if tilt:
        v[0] += tilt
    return unit(v)


def main() -> int:
    print(f"\n生产项目：{URL}")
    print("\033[2m本地（verify_rls_local.mjs）跳过了 embeddings 那个迁移，"
          "所以下面每一条都是它**唯一**的证据。\033[0m")

    tok_a, uid_a = sign_in(EMAIL_A)
    tok_b, uid_b = sign_in(EMAIL_B)
    if uid_a == uid_b:
        sys.exit("A 与 B 是同一个用户——跨用户那几条会平凡通过。换邮箱。")
    print(f"账号 A {uid_a}\n账号 B {uid_b}\n")

    created: list[tuple[str, str]] = []   # (path, id) 供清理

    # ============================================================ 0 · 表在不在
    print("\033[1m0 · 表结构\033[0m")
    for t in ("courses", "documents", "slices", "slice_embeddings"):
        s, _, raw = call("GET", f"/rest/v1/{t}?select=*&limit=1", tok_a)
        if not check("结构", f"{t} 存在且可查询", s == 200,
                     f"HTTP {s} {raw[:120]}" + ("  ← 迁移没跑？" if s == 404 else "")):
            return 1

    # ============================================================ 1 · 传参形状
    #
    # **这是本脚本存在的头号理由。** 其余各条都建立在它成立之上。
    print("\n\033[1m1 · vector(1024) 经 JSON 传参（第一步，错了后面全白跑）\033[0m")

    probe = unit([1.0] + [0.0] * (DIM - 1))

    # 形状 ①：JSON 数组。PostgREST 把它序列化成 `[1.0,0.0,...]` 再转 vector——
    # 那正是 pgvector 的文本输入格式，所以**推理上**成立。这一发把推理变成观测。
    s1, b1, raw1 = call("POST", "/rest/v1/rpc/match_slices", tok_a, payload={
        "p_course_id": str(uuid.uuid4()), "p_query": probe,
        "p_model": MODEL, "p_ruleset": RULESET, "p_limit": 1})
    array_ok = s1 == 200 and isinstance(b1, list)
    check("传参", "JSON 数组形式的 p_query 被接受", array_ok,
          f"HTTP {s1} {raw1[:160]}" if not array_ok else "← 推理成立，现在是观测")

    # 形状 ②：pgvector 的文本形式。数组不行时的退路。
    s2, b2, raw2 = call("POST", "/rest/v1/rpc/match_slices", tok_a, payload={
        "p_course_id": str(uuid.uuid4()), "p_query": "[" + ",".join(str(x) for x in probe) + "]",
        "p_model": MODEL, "p_ruleset": RULESET, "p_limit": 1})
    text_ok = s2 == 200 and isinstance(b2, list)
    check("传参", "文本形式 '[...]' 的 p_query 被接受", text_ok,
          f"HTTP {s2} {raw2[:160]}" if not text_ok else "")

    if not (array_ok or text_ok):
        print("\n\033[31m两种形状都不通。\033[0m 把 match_slices 的 p_query 改成 text 或 float[]，"
              "在函数体里显式 ::vector(1024)。**这是 schema 改动，改完重跑本脚本。**\n")
        return 1
    send_vec = (lambda v: v) if array_ok else (lambda v: "[" + ",".join(str(x) for x in v) + "]")
    print(f"  \033[2m→ 后续用{'JSON 数组' if array_ok else '文本'}形式\033[0m")

    # 空课程 id 应当返回空列表，而不是报错——这是下面「作用域」断言的前提。
    check("传参", "未知 course_id → 返回空列表而非报错", (b1 if array_ok else b2) == [],
          "← 它是「作用域」那几条的前提")

    # ============================================================ 2 · 造数据
    print("\n\033[1m2 · 造两门课，每门一份文档一个切片一条向量\033[0m")
    versions = {"parse_schema_version": "1.0", "slice_schema_version": "1.0",
                "slicer_version": "0.1.0", "slice_ruleset_version": "slice-1.0"}
    rep = {"Prefer": "return=representation"}

    def mk_course(tok: str, uid: str, name: str) -> str | None:
        s, b, raw = call("POST", "/rest/v1/courses", tok,
                         payload={"owner": uid, "name": name}, headers=rep)
        if s not in (200, 201) or not b:
            print(f"    建课失败（{s}）：{raw[:200]}")
            return None
        cid = b[0]["id"]
        created.append(("courses", cid))
        return cid

    def mk_doc(tok: str, uid: str, cid: str, sha: str) -> str | None:
        s, b, raw = call("POST", "/rest/v1/documents", tok, payload={
            "owner": uid, "course_id": cid, "filename": f"{sha}.pdf", "source_format": "pdf",
            "content_sha256": sha, "page_count": 1, "usable_page_count": 1,
            "verdict": "usable", "parser": {"service_version": "verify"}, **versions,
        }, headers=rep)
        if s not in (200, 201) or not b:
            print(f"    建文档失败（{s}）：{raw[:200]}")
            return None
        return b[0]["id"]

    def mk_slice_and_vec(tok: str, uid: str, did: str, sid: str, vec: list[float]) -> bool:
        s, _, raw = call("POST", "/rest/v1/slices", tok, payload={
            "document_id": did, "slice_id": sid, "owner": uid, "ordinal": 1,
            "page_indices": [1], "text": f"切片 {sid}", "char_count": 5, "units": [],
        })
        if s not in (200, 201, 204):
            print(f"    建切片失败（{s}）：{raw[:200]}")
            return False
        s, _, raw = call("POST", "/rest/v1/slice_embeddings", tok, payload={
            "document_id": did, "slice_id": sid, "owner": uid,
            "model": MODEL, "embed_ruleset": RULESET, "embedding": send_vec(vec),
        })
        if s not in (200, 201, 204):
            print(f"    写向量失败（{s}）：{raw[:200]}")
            return False
        return True

    course1 = mk_course(tok_a, uid_a, "验收课程 1")
    course2 = mk_course(tok_a, uid_a, "验收课程 2")
    if not (course1 and course2):
        return 1
    d1a = mk_doc(tok_a, uid_a, course1, f"sha-{uuid.uuid4().hex[:16]}")
    d1b = mk_doc(tok_a, uid_a, course1, f"sha-{uuid.uuid4().hex[:16]}")   # 同课第二份讲义
    d2 = mk_doc(tok_a, uid_a, course2, f"sha-{uuid.uuid4().hex[:16]}")
    if not (d1a and d1b and d2):
        return 1

    # 三条向量，**互相正交**，所以谁离查询近是算得出来的，不是碰运气：
    #   课1文档a → e1（查 e1 时它最近）
    #   课1文档b → e2，**slice_id 与文档 a 的同名**（'s001'）→ 专门用来验 document_id
    #   课2      → e3
    ok_vecs = (mk_slice_and_vec(tok_a, uid_a, d1a, "s001", basis(1))
               and mk_slice_and_vec(tok_a, uid_a, d1b, "s001", basis(2))
               and mk_slice_and_vec(tok_a, uid_a, d2, "s001", basis(3)))
    check("传参", "向量写得进 slice_embeddings", ok_vecs,
          "← 写入侧的传参形状；它与 RPC 的传参是两条路径" if ok_vecs else "")
    if not ok_vecs:
        return 1

    # ============================================================ 3 · 作用域与返回形状
    print("\n\033[1m3 · 作用域（一门课）与返回形状\033[0m")
    s, rows, raw = call("POST", "/rest/v1/rpc/match_slices", tok_a, payload={
        "p_course_id": course1, "p_query": send_vec(basis(1)),
        "p_model": MODEL, "p_ruleset": RULESET, "p_limit": 10})
    rows = rows if isinstance(rows, list) else []
    check("排序", "查课程 1 有结果", s == 200 and len(rows) > 0, f"HTTP {s} {len(rows)} 行 {raw[:120]}")

    check("排序", "同一门课的**两份讲义都召回**", len({r["document_id"] for r in rows}) == 2,
          f"{len({r['document_id'] for r in rows})} 份 ← 这是「跨课不召回」的允许控制；"
          "少了它，一个什么都召不回的实现也会让下一条全绿")

    check("结构", "返回里带 document_id", all("document_id" in r for r in rows),
          "← slice_id 是文档内局部 id，两份讲义都有 's001'；只返回 slice_id 分不开")
    check("结构", "两行的 slice_id 确实同名", len({r["slice_id"] for r in rows}) == 1,
          "← 所以上一条不是空谈")

    s, rows2, _ = call("POST", "/rest/v1/rpc/match_slices", tok_a, payload={
        "p_course_id": course2, "p_query": send_vec(basis(1)),
        "p_model": MODEL, "p_ruleset": RULESET, "p_limit": 10})
    rows2 = rows2 if isinstance(rows2, list) else []
    check("排序", "查课程 2 **召不回课程 1 的切片**",
          all(r["document_id"] == d2 for r in rows2) and len(rows2) == 1,
          f"{len(rows2)} 行，全部来自课程 2 的文档" if rows2 else "0 行 ← 作用域可能过紧")

    # ============================================================ 4 · 排序语义
    print("\n\033[1m4 · 排序语义（<=> 真的在按相似度排）\033[0m")
    # 查询向量取 e1，期望 e1 的那个切片排第一、相似度接近 1；e2 的接近 0。
    s, rows3, _ = call("POST", "/rest/v1/rpc/match_slices", tok_a, payload={
        "p_course_id": course1, "p_query": send_vec(basis(1)),
        "p_model": MODEL, "p_ruleset": RULESET, "p_limit": 10})
    rows3 = rows3 if isinstance(rows3, list) else []
    if check("排序", "拿到两行用于比较", len(rows3) == 2, f"{len(rows3)} 行"):
        top, second = rows3[0], rows3[1]
        check("排序", "最近的那条排第一", top["document_id"] == d1a,
              f"top={top['document_id'][:8]} 期望={d1a[:8]}")
        check("排序", "相似度降序", top["similarity"] >= second["similarity"],
              f"{top['similarity']:.4f} ≥ {second['similarity']:.4f}")
        # 判据跑前定死：e1·e1 = 1，e1·e2 = 0（两条正交单位向量）。容差 1e-3 给浮点。
        check("排序", "相似度的数值对得上（正交向量，期望 1 与 0）",
              abs(top["similarity"] - 1.0) < 1e-3 and abs(second["similarity"]) < 1e-3,
              f"{top['similarity']:.4f} / {second['similarity']:.4f} "
              "← 对不上说明归一化或 <=> 的用法有问题，不是「检索不准」")

    # ============================================================ 5 · RLS
    print("\n\033[1m5 · RLS（跨用户）\033[0m")
    s, rows4, _ = call("POST", "/rest/v1/rpc/match_slices", tok_b, payload={
        "p_course_id": course1, "p_query": send_vec(basis(1)),
        "p_model": MODEL, "p_ruleset": RULESET, "p_limit": 10})
    check("RLS", "B 用 A 的 course_id 调 match_slices → 0 行",
          s == 200 and rows4 == [], f"HTTP {s} {rows4}")
    s, rowsb, _ = call("GET", f"/rest/v1/slice_embeddings?select=slice_id&limit=5", tok_b)
    check("RLS", "B 直接查 slice_embeddings → 看不到 A 的行",
          s == 200 and rowsb == [], f"HTTP {s} {rowsb}")
    # 允许控制：A 自己查得到。少了它，「B 看不到」在整表不可读时同样成立。
    s, rowsa, _ = call("GET", f"/rest/v1/slice_embeddings?select=slice_id&limit=5", tok_a)
    check("RLS", "而 A 查得到自己的（允许控制）", s == 200 and len(rowsa or []) == 3,
          f"{len(rowsa or [])} 行 ← 少了它，上一条在整表不可读时也绿")

    # ============================================================ 6 · cascade + 清理
    print("\n\033[1m6 · cascade（删课 → 文档/切片/向量一起没）\033[0m")
    call("DELETE", f"/rest/v1/courses?id=eq.{course1}", tok_a)
    s, left, _ = call("GET", f"/rest/v1/documents?select=id&course_id=eq.{course1}", tok_a)
    check("结构", "删课程 1 → 它的文档没了", left == [], f"{left}")
    s, leftv, _ = call("GET", f"/rest/v1/slice_embeddings?select=slice_id", tok_a)
    check("结构", "它的向量也没了，而课程 2 的还在", len(leftv or []) == 1,
          f"剩 {len(leftv or [])} 条 ← 控制项：不是整表被清空")

    call("DELETE", f"/rest/v1/courses?id=eq.{course2}", tok_a)
    s, final, _ = call("GET", "/rest/v1/courses?select=id", tok_a)
    print(f"\n  {LAYERS['清理']} 验收课程已删除，A 名下剩 {len(final or [])} 门课")

    print(f"\n\033[1m合计\033[0m  绿 {PASSED} · 红 {FAILED}")
    print("\033[2m这批数字与 verify_rls_local.mjs 的本地绿数**不相加**——"
          "一个是真库观测，一个是本地机制验证（同口径 F34）。\033[0m\n")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
