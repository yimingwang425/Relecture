"""检索评测集 v2（100 题）的**候选生成**与**答案页补全**（2026-10-15）。

    api/.venv/bin/python 实验脚本/检索评测集/v2/生成.py pages      # 导出 54 页原文（不花钱）
    api/.venv/bin/python 实验脚本/检索评测集/v2/生成.py generate   # 出候选题（Sonnet，要钱）
    api/.venv/bin/python 实验脚本/检索评测集/v2/生成.py gold       # 第二遍：通读全部页，补全答案页（要钱）
    api/.venv/bin/python 实验脚本/检索评测集/v2/生成.py select     # 机械检查 + 分层抽 100 题 + 备选（不花钱）
    api/.venv/bin/python 实验脚本/检索评测集/v2/生成.py sheet      # 人工复核表（不花钱）
    api/.venv/bin/python 实验脚本/检索评测集/v2/生成.py freeze     # 读复核后的表 → 评测集.json（不花钱）

## 为什么还是让模型出题

v1（`../README.md` §4.1）的硬要求是「不从切片文本反向生成问题」——挑句子改问句，检索必中，
测出来的是字符串匹配。v2 要 100 题，人手写不现实，所以由模型出候选，但守三道关：

1. **出题 prompt** 照 v1 的写法：先读懂这一页讲什么，再用学生的说法问；给 v1 那张「课件说法 → 本集问法」对照表当样例。
2. **机械检查**（`select`）：除「精确术语」一类外，问题里的英文词不得出现在答案页原文里（v1 `校验.py` 第 4 项，同一条）。
3. **人工复核**（`sheet`）：中文的语义复用机器挡不住，靠人看。

## 答案页为什么要第二遍

出题时模型只盯着一两页，**别的页也能答**的情况它不知道——那时「只标了一页」会把检索命中别页算成错。
第二遍换一个 prompt、给全部 54 页，让模型列出「完整回答需要哪些页」。两遍不一致的题进复核表由人裁定。

要钱的子命令要 `ANTHROPIC_API_KEY`（单条命令里映射，不 export）。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "api"))
import api  # noqa: E402,F401  —— 注入 explain / parse_service / slicer 路径

CORPUS = REPO / "实验脚本" / "测试集"
DOCS = {"22.081J": "MIT22_081JF10_lec01b.pdf", "11.201": "MIT11_201F07_slides2.pdf"}
V1 = REPO / "实验脚本" / "检索评测集" / "评测集.json"

PAGES = HERE / "pages.json"
CANDIDATES = HERE / "候选.json"
SELECTED = HERE / "选题.json"
SHEET = HERE / "复核表.md"
FROZEN = HERE / "评测集.json"

MODEL = "claude-sonnet-5"
SEED = 20261015

#: 最终 100 题的分层（方案见对话 2026-10-15，有答案 90 题 + 无答案 10 题）。
TARGET = {"synonym": 35, "multipage": 25, "exact": 15, "multiturn": 15, "no_answer": 10}
#: 每类多留几道备选：人工复核时删掉的题按顺序由备选补上，补法预先定死。
RESERVE = {"synonym": 6, "multipage": 5, "exact": 3, "multiturn": 3, "no_answer": 2}
#: 同一页最多当几道题的答案页（只数有答案的题）。54 页 90 题，不设上限的话会扎堆在好出题的几页上。
MAX_PER_PAGE = 4


# ---------------------------------------------------------------- 语料


def cmd_pages(_args: Any) -> int:
    from parse_service.pdf import parse_pdf
    from slicer import slice_document

    out = []
    for doc, fname in DOCS.items():
        parsed = parse_pdf((CORPUS / fname).read_bytes(), fname)
        for s in slice_document(parsed).slices:
            assert len(s.page_indices) == 1, "v2 假定一页一片（实测如此）；不成立就要改答案页的口径"
            out.append({"doc": doc, "source_file": fname, "page_index": s.page_indices[0],
                        "title": s.title, "text": s.text})
    PAGES.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(f"{len(out)} 页 → {PAGES}")
    return 0


def pages() -> list[dict[str, Any]]:
    return json.loads(PAGES.read_text("utf-8"))


def ref(p: dict[str, Any]) -> str:
    return f"{p['doc']} p{p['page_index']}"


def render_corpus(ps: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"### [{ref(p)}]\n{(p['title'] + chr(10)) if p['title'] else ''}{p['text']}" for p in ps)


# ---------------------------------------------------------------- 出题

STYLE = """出题的写法（最重要）：
- 先读懂课件讲了什么，再用**学生自己的说法**用中文提问。学生没读过这页课件，不知道课件用的术语。
- **不要**把课件里的句子改成问句，**不要**复用课件里的英文术语、英文短语或关键名词短语。
- 好的样例（左边是课件的说法，右边是学生的问法）：
  - heat engine efficiency, η=(1−Te/Ti) → 为什么不能把热全都变成电
  - enrichment → 为什么还要先加工一道
  - power density → 同样一平方米各能出多少电
  - energy carrier → 它本身算是一种能源吗
  - 规划的 stakeholder / 授权 → 到底谁说了算
  - DESIGN tradition / EMPOWERMENT tradition → 主要是画图纸的，还是跟各方博弈的"""

TASKS: dict[str, str] = {
    "synonym": """出 {n} 道**单页**题：每道题的答案完整地在某**一页**上。
{style}
输出 JSON 数组，每项 {{"question": "...", "gold": ["<文档> p<页号>"], "why": "答案页上哪句话回答了它（中文转述）"}}。""",
    "multipage": """出 {n} 道**跨页**题：要完整回答，**必须同时用到 2–3 页**的内容（例如比较两页各讲的一样东西、把分散在几页的步骤串起来）。
只用其中一页答不全。页必须在同一份课件里。
{style}
输出 JSON 数组，每项 {{"question": "...", "gold": ["<文档> p<页号>", "<文档> p<页号>"], "why": "每页各提供了什么"}}。""",
    "exact": """出 {n} 道**精确词**题：问题里**原样**带着课件里的一个精确的东西——缩写、公式符号、数字、专有名词、
英文术语（例如「U-235 占多少」「Te 和 Ti 分别指什么」）。其余部分仍用中文、学生的口吻。
这一类专门测「按字面找得到」，所以这里**允许**复用课件原词，但只复用那一个精确的东西，不要整句照搬。
答案在一页或两页上。
输出 JSON 数组，每项 {{"question": "...", "gold": ["<文档> p<页号>"], "exact_term": "原样带进问题的那个词", "why": "..."}}。""",
    "multiturn": """出 {n} 道**多轮追问**题：先有 1–2 轮对话（学生问、助手简短回答），最后一句是学生的追问，
**追问里用「它」「这个」「那」「刚才说的」之类指代前文**，单看这一句不知道在问什么。
追问的答案在课件的一页或两页上，而且**不是**前文那一轮已经回答过的那一页。
{style}
助手的回答写 1–2 句即可，不要把追问的答案提前说出来。
输出 JSON 数组，每项 {{"history": [{{"role": "user", "content": "..."}}, {{"role": "assistant", "content": "..."}}],
"question": "追问", "gold": ["<文档> p<页号>"], "why": "..."}}。""",
    "no_answer": """出 {n} 道**课件里没有答案**的题：话题与这份课件相关、学生很可能会问，但课件**任何一页都没有**回答它
（例如课件提了一句却没展开、或者是相邻的知识点）。最好是那种检索器容易误以为能答的题。
{style}
输出 JSON 数组，每项 {{"question": "...", "gold": [], "near": ["最容易被误当成答案的页"], "why": "为什么课件答不了"}}。""",
}

#: 每类每份课件出多少候选。按页数大致 7:3 分（39 页 vs 15 页），约为目标数的 1.7 倍，给机械检查与抽样留余量。
PLAN = {
    "synonym": {"22.081J": 42, "11.201": 18},
    "multipage": {"22.081J": 30, "11.201": 14},
    "exact": {"22.081J": 18, "11.201": 8},
    "multiturn": {"22.081J": 18, "11.201": 8},
    "no_answer": {"22.081J": 12, "11.201": 6},
}

GEN_SYSTEM = """你在为一个「课件讲解助手」的**检索**评测集出题。学生上传英文课件、用中文提问，系统要找到能回答问题的那几页。
你会读到一份课件的全部页面（每页以 [文档 p页号] 开头）。按要求出题，只输出 JSON，不要其他文字。
JSON 字符串里需要引号时一律用「」，**不要用英文双引号**（会让 JSON 不合法）。
题目之间不要重复，尽量分散到不同的页上，不要集中在少数几页。下面这些题已经在旧评测集里了，不要出意思相同的：
{v1}"""


def _model(max_tokens: int = 8000):
    from explain.llm import AnthropicModel

    return AnthropicModel(name=MODEL, effort="medium", thinking="disabled", max_tokens=max_tokens,
                          cache_system=True, cache_conversation=False)


def _json_array(text: str) -> list[dict[str, Any]]:
    a, b = text.find("["), text.rfind("]")
    try:
        data = json.loads(text[a:b + 1])
    except json.JSONDecodeError:
        # 原样留下来再抛：花了钱的输出不能丢，读完能看出是哪里坏了
        (HERE / "_bad_output.txt").write_text(text, "utf-8")
        raise
    assert isinstance(data, list)
    return data


def cmd_generate(args: Any) -> int:
    ps = pages()
    v1 = "\n".join(f"- {it['question']}" for it in json.loads(V1.read_text("utf-8"))["items"])
    done = json.loads(CANDIDATES.read_text("utf-8")) if CANDIDATES.exists() else {"items": [], "cost_usd": 0.0}
    have = {(it["category"], it["doc"]) for it in done["items"]}
    m = _model()
    plan = PLAN
    if args.extra:
        # 补出：某一类机械检查后不够数时再出一批，id 带批次后缀，原有候选一条不动
        cat, doc, n = args.extra[0], args.extra[1], int(args.extra[2])
        plan, have = {cat: {doc: n}}, set()
    batch = args.batch or ""
    for cat, per_doc in plan.items():
        for doc, n in per_doc.items():
            if (cat, doc) in have:
                continue
            system = GEN_SYSTEM.format(v1=v1) + "\n\n## 课件\n\n" + render_corpus([p for p in ps if p["doc"] == doc])
            prompt = TASKS[cat].format(n=n, style=STYLE)
            c = m.complete(system, [{"role": "user", "content": prompt}])
            items = _json_array(c.text)
            for k, it in enumerate(items):
                done["items"].append({"id": f"{cat}-{doc}-{batch}{k:02d}", "category": cat, "doc": doc, **it})
            done["cost_usd"] += c.cost or 0.0
            CANDIDATES.write_text(json.dumps(done, ensure_ascii=False, indent=1), "utf-8")
            print(f"  {cat:<10} {doc:<8} {len(items):>3} 条  累计 ${done['cost_usd']:.3f}")
    print(f"候选共 {len(done['items'])} 条 → {CANDIDATES}")
    return 0


# ---------------------------------------------------------------- 答案页第二遍

GOLD_SYSTEM = """你在核对一个检索评测集的**答案页**。下面是两份课件的全部页面（每页以 [文档 p页号] 开头）。
对每道题，判断**完整回答它需要哪些页**：
- `required`：缺了它就答不全的页。一道题的答案如果在两页上各有一部分，两页都要列。
- `also`：讲到了相关内容、但不是回答所必需的页（可以为空）。
- 课件里根本答不了的题，`required` 为空。
多轮题看的是**最后那句追问**在前文语境下问的是什么。
JSON 字符串里需要引号时一律用「」，不要用英文双引号。
只输出 JSON 数组，每项 {{"id": "...", "required": ["<文档> p<页号>"], "also": [...], "note": "一句话"}}。

## 课件

{corpus}"""


def cmd_gold(_args: Any) -> int:
    ps = pages()
    cand = json.loads(CANDIDATES.read_text("utf-8"))
    todo = [it for it in cand["items"] if "gold2" not in it]
    m = _model(6000)
    system = GOLD_SYSTEM.format(corpus=render_corpus(ps))
    for i in range(0, len(todo), 12):
        batch = todo[i:i + 12]
        lines = []
        for it in batch:
            hist = "".join(f"\n  {'学生' if h['role'] == 'user' else '助手'}：{h['content']}" for h in it.get("history", []))
            lines.append(f"- id={it['id']}{('（前文：' + hist + '）') if hist else ''}\n  题：{it['question']}")
        c = m.complete(system, [{"role": "user", "content": "\n".join(lines)}])
        got = {r["id"]: r for r in _json_array(c.text)}
        for it in batch:
            r = got.get(it["id"])
            if r is not None:
                it["gold2"] = {"required": r.get("required", []), "also": r.get("also", []), "note": r.get("note", "")}
        cand["cost_usd"] += c.cost or 0.0
        CANDIDATES.write_text(json.dumps(cand, ensure_ascii=False, indent=1), "utf-8")
        print(f"  {i + len(batch)}/{len(todo)}  累计 ${cand['cost_usd']:.3f}")
    return 0


# ---------------------------------------------------------------- 机械检查与抽样


def _norm_ref(s: str) -> str | None:
    m = re.match(r"\s*\[?\s*(22\.081J|11\.201)\s*p\s*(\d+)\s*\]?\s*$", s)
    return f"{m.group(1)} p{int(m.group(2))}" if m else None


def check(it: dict[str, Any], by_ref: dict[str, dict[str, Any]]) -> list[str]:
    """机械检查。返回问题列表；空 = 通过。**只挡确定的错**，拿不准的交给人工复核。"""
    errs = []
    gold = [_norm_ref(g) for g in it.get("gold", [])]
    if any(g is None or g not in by_ref for g in gold):
        errs.append(f"答案页不存在：{it.get('gold')}")
        return errs
    cat = it["category"]
    if cat == "no_answer" and gold:
        errs.append("无答案题却给了答案页")
    if cat != "no_answer" and not gold:
        errs.append("没有答案页")
    if cat == "multipage" and len(set(gold)) < 2:
        errs.append("跨页题只有一页")
    if cat == "multiturn" and not it.get("history"):
        errs.append("多轮题没有前文")
    if cat != "exact" and gold:
        # v1 校验.py 第 4 项：问题里的英文词不得出现在答案页原文里
        page_text = " ".join((by_ref[g]["title"] or "") + " " + by_ref[g]["text"] for g in gold).lower()
        toks = set(re.findall(r"[A-Za-z]{3,}", it["question"].lower()))
        overlap = sorted(t for t in toks if t in page_text)
        if overlap:
            errs.append(f"问题里的英文词出现在答案页原文里：{overlap}")
    if cat == "exact":
        term = str(it.get("exact_term") or "")
        if not term or term not in it["question"]:
            errs.append("精确词题的问题里没有原样带着那个词")
    return errs


def agreement(it: dict[str, Any]) -> str:
    """两遍答案页一致吗。`same` / `differs` / `missing`（第二遍没给）。"""
    g2 = it.get("gold2")
    if g2 is None:
        return "missing"
    a = {_norm_ref(g) for g in it.get("gold", [])}
    b = {_norm_ref(g) for g in g2.get("required", [])}
    return "same" if a == b else "differs"


def cmd_select(_args: Any) -> int:
    """分层抽样：每类在通过机械检查的候选里**按固定种子打乱**，依次取，同一页当答案页不超过 MAX_PER_PAGE 次。
    前 TARGET[类] 道入选，后 RESERVE[类] 道是备选（人工删题时按顺序补）。两遍答案页不一致的题**照样可以入选**——
    它们进复核表由人裁定，不在这里按一致性筛（按一致性筛会系统性地偏向容易的题）。"""
    by_ref = {ref(p): p for p in pages()}
    cand = json.loads(CANDIDATES.read_text("utf-8"))["items"]
    rng = random.Random(SEED)
    load: dict[str, int] = {}
    chosen, reserve, rejected = [], [], []
    for cat in TARGET:
        pool = [it for it in cand if it["category"] == cat]
        rng.shuffle(pool)
        taken = 0
        for it in pool:
            errs = check(it, by_ref)
            if errs:
                rejected.append({"id": it["id"], "errors": errs})
                continue
            gold = sorted({_norm_ref(g) for g in it["gold"]})
            if any(load.get(g, 0) >= MAX_PER_PAGE for g in gold):
                rejected.append({"id": it["id"], "errors": ["答案页已满（同一页最多 4 道）"]})
                continue
            row = {**it, "gold": gold, "agreement": agreement(it)}
            if taken < TARGET[cat]:
                chosen.append(row)
            elif taken < TARGET[cat] + RESERVE[cat]:
                reserve.append(row)
            else:
                break
            for g in gold:
                load[g] = load.get(g, 0) + 1
            taken += 1
        if taken < TARGET[cat]:
            print(f"⚠️ {cat} 只凑到 {taken}/{TARGET[cat]} 道")
    SELECTED.write_text(json.dumps({"seed": SEED, "chosen": chosen, "reserve": reserve, "rejected": rejected},
                                   ensure_ascii=False, indent=1), "utf-8")
    from collections import Counter

    print(f"入选 {len(chosen)}，备选 {len(reserve)}，机械检查挡掉 {len(rejected)}")
    print("入选按类：", dict(Counter(r["category"] for r in chosen)))
    print("两遍答案页：", dict(Counter(r["agreement"] for r in chosen)))
    print("答案页覆盖：", len({g for r in chosen for g in r["gold"]}), "/ 54 页")
    return 0


# ---------------------------------------------------------------- 复核表

CAT_NAME = {"synonym": "同义改写", "multipage": "跨页", "exact": "精确词", "multiturn": "多轮追问", "no_answer": "无答案"}


def cmd_sheet(_args: Any) -> int:
    by_ref = {ref(p): p for p in pages()}
    sel = json.loads(SELECTED.read_text("utf-8"))
    out = ["# 检索评测集 v2 · 人工复核表（100 题 + 备选）", "",
           "**怎么复核**：**默认保留**这道题、答案页按「出题时」那一栏。只在有问题时动手：",
           "- 题目不像学生会问的、意思不清、或照搬了课件原句（「精确词」一类允许原样带一个词）→ 勾 **删掉**",
           "  （由同类备选按顺序补上）。",
           "- 答案页不对 → 在 **改成** 后面写完整回答这道题**需要**的全部页，不多不少，如 `22.081J p20, p21`；",
           "  无答案题若课件其实答得了，写出那几页。",
           "- 每题看完勾 **已看**——没勾的题当作没复核，不进评测集（上次全空的那条分不清是漏看还是判了不符合）。",
           "",
           "⚠️ 标了 **两遍不一致** 的题：出题时标的页与第二遍通读全部页后认为需要的页不同，两份都列出来了。",
           "这种题**必须**在「改成」里写出你认定的页（照抄其中一份也行）。",
           "页面原文附在每题下面，只附相关的页。", "", "---", ""]

    def block(i: int, r: dict[str, Any], tag: str = "") -> list[str]:
        lines = [f"## {tag}{i}. `{r['id']}`　{CAT_NAME[r['category']]}" + ("　⚠️ **两遍不一致**" if r["agreement"] == "differs" else ""), ""]
        for h in r.get("history", []):
            lines.append(f"> {'学生' if h['role'] == 'user' else '助手'}：{h['content']}")
        if r.get("history"):
            lines.append(">")
        lines += [f"> **学生：{r['question']}**", "",
                  f"- 答案页（出题时）：{', '.join(r['gold']) or '（无）'}"]
        g2 = r.get("gold2") or {}
        if r["agreement"] == "differs":
            lines.append(f"- 答案页（第二遍通读）：{', '.join(g2.get('required', [])) or '（无）'}　备注：{g2.get('note', '')}")
        if r.get("exact_term"):
            lines.append(f"- 精确词：`{r['exact_term']}`")
        lines.append(f"- 出题理由：{r.get('why', '')}")
        show = list(dict.fromkeys([*r["gold"], *[x for x in (_norm_ref(s) for s in g2.get("required", [])) if x],
                                   *[x for x in (_norm_ref(s) for s in r.get("near", [])) if x]]))
        for g in show:
            if g in by_ref:
                p = by_ref[g]
                body = ((p["title"] + " ｜ ") if p["title"] else "") + p["text"].replace("\n", " ")
                lines.append(f"  - `{g}`：{body}")
        lines += ["", "- [ ] 已看", "- [ ] 删掉", "- 改成：", "", "备注：", "", "---", ""]
        return lines

    for i, r in enumerate(sel["chosen"], 1):
        out += block(i, r)
    out += ["", "# 备选（只在上面删题时用，按顺序补同类的第一道）", ""]
    for i, r in enumerate(sel["reserve"], 1):
        out += block(i, r, "备")
    SHEET.write_text("\n".join(out), "utf-8")
    print(f"→ {SHEET}")
    return 0


# ---------------------------------------------------------------- 冻结


def _parse_refs(text: str) -> list[str]:
    """「22.081J p20, p21」「11.201 p3」→ 规范引用。后面的页省略文档名时沿用前一个。"""
    out, doc = [], None
    for m in re.finditer(r"(22\.081J|11\.201)?\s*p\s*(\d+)", text):
        doc = m.group(1) or doc
        if doc:
            out.append(f"{doc} p{int(m.group(2))}")
    return out


def review_marks() -> dict[str, dict[str, Any]]:
    """复核表 → {id: {seen, delete, fix}}。`fix` 是「改成」后面解析出的页（None = 没填）。"""
    marks = {}
    for b in re.split(r"\n## ", SHEET.read_text("utf-8"))[1:]:
        m = re.search(r"`([^`]+)`", b)
        if not m:
            continue
        box = lambda name: bool(re.search(rf"^- \[\s*[xX]\s*\] *{name}", b, re.M))  # noqa: E731
        fix = re.search(r"^- 改成：(.*)$", b, re.M)
        raw = fix.group(1).strip() if fix else ""
        marks[m.group(1)] = {"seen": box("已看"), "delete": box("删掉"),
                             "fix": _parse_refs(raw) if raw else None, "fix_raw": raw,
                             "none": bool(re.search(r"无|没有|空", raw))}
    return marks


def cmd_freeze(args: Any) -> int:
    """规则（写在复核之前，2026-10-15）：

    1. 没勾「已看」的题不进评测集，也不用备选补（那是漏看，不是删题）。
    2. 勾了「删掉」的题，由**同类备选按顺序**第一道「已看、没删」的补上。
    3. 「改成」写了页 → 答案页以它为准；写了「无」→ 答案页为空。
    4. 两遍不一致却没填「改成」→ 按规则 1 处理（未裁定 = 没复核完）。
    5. 改完后类别不成立的题（无答案题被改成有页、跨页题只剩一页、有答案题被改成无）→ 按「删掉」处理，由备选补。
    """
    by_ref = {ref(p): p for p in pages()}
    sel = json.loads(SELECTED.read_text("utf-8"))
    log: dict[str, list[str]] = {"未复核": [], "删掉": [], "改了答案页": [], "类别不成立": [], "备选补入": []}
    if args.confirmed_all:
        # 用户 2026-10-15 在对话里确认：复核表全部看过、没有要删的、没有要改的，不再回填方框推一遍。
        # 等价于每题勾「已看」、不删、不改；两遍不一致的题按「出题时」的答案页（第二遍的另存为 gold_pages_alt，
        # 结果里另报一份按它算的敏感性分析）。
        marks = {r["id"]: {"seen": True, "delete": False, "fix": list(r["gold"]), "fix_raw": "", "none": not r["gold"]}
                 for r in [*sel["chosen"], *sel["reserve"]]}
        log["用户确认"] = ["2026-10-15 对话：全部已看、无删除、无改动；两遍不一致的按出题时的答案页"]
    else:
        marks = review_marks()

    def resolve(r: dict[str, Any]) -> tuple[str, list[str]]:
        """→ (状态, 答案页)。状态：ok / unseen / delete。"""
        mk = marks.get(r["id"], {})
        if not mk.get("seen"):
            return "unseen", []
        if mk.get("delete"):
            return "delete", []
        if r["agreement"] == "differs" and mk.get("fix") is None and not mk.get("none"):
            return "unseen", []
        gold = r["gold"]
        if mk.get("fix"):
            gold = sorted(set(mk["fix"]))
            if gold != r["gold"]:
                log["改了答案页"].append(f"{r['id']}：{r['gold']} → {gold}")
        elif mk.get("none"):
            gold = []
        cat = r["category"]
        if (cat == "no_answer") != (not gold) or (cat == "multipage" and len(gold) < 2):
            log["类别不成立"].append(f"{r['id']}：{cat}，答案页 {gold}")
            return "delete", []
        if any(g not in by_ref for g in gold):
            raise SystemExit(f"{r['id']} 的「改成」里有不存在的页：{gold}")
        return "ok", gold

    reserve = {cat: [r for r in sel["reserve"] if r["category"] == cat] for cat in TARGET}
    final = []
    for r in sel["chosen"]:
        st, gold = resolve(r)
        if st == "unseen":
            log["未复核"].append(r["id"])
            continue
        if st == "ok":
            final.append((r, gold))
            continue
        log["删掉"].append(r["id"])
        while reserve[r["category"]]:
            alt = reserve[r["category"]].pop(0)
            st2, gold2 = resolve(alt)
            if st2 == "ok":
                final.append((alt, gold2))
                log["备选补入"].append(f"{alt['id']}（替 {r['id']}）")
                break

    def page(g: str) -> dict[str, Any]:
        p = by_ref[g]
        return {"source_file": p["source_file"], "page_index": p["page_index"]}

    items = [{"id": f"V2-{i:03d}", "source_id": r["id"], "question": r["question"],
              **({"history": r["history"]} if r.get("history") else {}),
              "gold_pages": [page(g) for g in gold], "tags": [r["category"]],
              **({"gold_pages_alt": [page(g) for g in sorted({x for x in (_norm_ref(s) for s in r["gold2"]["required"]) if x})]}
                 if r["agreement"] == "differs" else {}),
              **({"exact_term": r["exact_term"]} if r.get("exact_term") else {})}
             for i, (r, gold) in enumerate(final, 1)]
    FROZEN.write_text(json.dumps({"version": "v2", "created": "2026-10-15", "annotator": "1 人（模型出题 + 人工复核）",
                                  "corpus": list(DOCS.values()), "gold_granularity": "page",
                                  "freeze_log": log, "items": items}, ensure_ascii=False, indent=1), "utf-8")
    from collections import Counter

    print(f"评测集 {len(items)} 题 → {FROZEN}")
    print("按类：", dict(Counter(it["tags"][0] for it in items)))
    for k, v in log.items():
        print(f"  {k}：{len(v)}" + (f"　{v[:6]}{' …' if len(v) > 6 else ''}" if v else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pages", "gold", "select", "sheet"):
        sub.add_parser(name)
    fz = sub.add_parser("freeze")
    fz.add_argument("--confirmed-all", action="store_true",
                    help="用户已在对话里确认全部看过、无删无改（不读方框）")
    g = sub.add_parser("generate")
    g.add_argument("--extra", nargs=3, metavar=("CATEGORY", "DOC", "N"), help="补出一批")
    g.add_argument("--batch", default="", help="补出批次的 id 后缀，如 b")
    args = ap.parse_args()
    return {"pages": cmd_pages, "generate": cmd_generate, "gold": cmd_gold,
            "select": cmd_select, "sheet": cmd_sheet, "freeze": cmd_freeze}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
