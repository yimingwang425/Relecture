"""评测集校验：gold 页存在性、合规排除、标签与页面事实的一致性、反向生成检查。

用法（需 slicer 的 venv，因为要调 parse-service 与 slicer）::

    ../../slicer/.venv/bin/python 校验.py

四项检查：

1. **gold 页存在且可定位**——文件在 `测试集/` 里、页号在范围内。
2. **合规排除**——不得引用 15.316（整份扫描件，解析层已拒绝）、
   不得引用 14.129 的 8 页第三方保留权利内容（`测试集/SOURCES.md` 要求评测输入跳过）。
3. **标签与页面事实一致**——标 `unreadable_page` 的页必须真的不产生切片，
   标 `figure_heavy` 的页必须仍是可用页，标 `multipage` 的必须真有多页。
   标签是靠人写的，写错了整个分组统计就废了，故用解析结果反查。
4. **反向生成检查**——问题中的英文词不得出现在 gold 页原文里。
   这是「不要从切片文本反向生成问题」那条硬要求的机器化下限：
   它只能挡住英文词的字面复用，**挡不住中文的语义复用**，后者仍需人工把关。
"""

import json, re, sys
from pathlib import Path
from parse_service.pdf import parse_pdf
from slicer import slice_document

C = Path('/Users/wangyiming/MyProjects/AI课件讲解助手/实验脚本/测试集')
E = Path('/Users/wangyiming/MyProjects/AI课件讲解助手/实验脚本/检索评测集/评测集.json')
RESTRICTED = {'MIT14_129S25_lec02.pdf': {10, 11, 25, 26, 31, 38, 40, 42}}
BANNED_FILE = 'MIT15_316SU05_leader_dev.pdf'

d = json.load(open(E)); items = d['items']
cache = {}
def doc(f):
    if f not in cache:
        r = parse_pdf((C/f).read_bytes(), f)
        cache[f] = (r, {p.index: p for p in r.pages},
                    {s.page_indices[0]: s for s in slice_document(r).slices})
    return cache[f]

errs = []
print(f"{'id':<6} {'tags':<28} {'gold':<34} {'kind':<13} {'切片':<5} chars  cov")
for it in items:
    tags = ','.join(it['tags'])
    if not it['gold_pages']:
        print(f"{it['id']:<6} {tags:<28} {'(空)':<34}")
        if 'no_answer' not in it['tags']: errs.append(f"{it['id']} 空 gold 但无 no_answer 标签")
        continue
    for g in it['gold_pages']:
        f, i = g['source_file'], g['page_index']
        if f == BANNED_FILE: errs.append(f"{it['id']} 引用了 15.316")
        if i in RESTRICTED.get(f, ()): errs.append(f"{it['id']} 引用受限页 {f} p{i}")
        if not (C/f).exists(): errs.append(f"{it['id']} 文件不存在 {f}"); continue
        r, pages, slices = doc(f)
        if i not in pages: errs.append(f"{it['id']} 页不存在 {f} p{i}"); continue
        p = pages[i]
        has = '有' if i in slices else '无'
        print(f"{it['id']:<6} {tags:<28} {f[3:9]+' p'+str(i):<34} {p.kind:<13} {has:<5} "
              f"{p.evidence.text_char_count:>5}  {p.evidence.image_coverage_union:.2f}")
        # 标签与页面事实的一致性
        if 'unreadable_page' in it['tags'] and p.usable:
            errs.append(f"{it['id']} 标 unreadable_page 但 {f} p{i} 可用")
        if 'figure_heavy' in it['tags'] and not p.usable:
            errs.append(f"{it['id']} 标 figure_heavy 但 {f} p{i} 不可用（应为 unreadable_page）")
    if 'multipage' in it['tags'] and len(it['gold_pages']) < 2:
        errs.append(f"{it['id']} 标 multipage 但只有 1 页")
    if len(it['gold_pages']) > 1 and 'multipage' not in it['tags'] and 'figure_heavy' not in it['tags']:
        errs.append(f"{it['id']} 多页但未标 multipage")

print('\n=== 反向生成检查：问题中的英文词是否出现在 gold 页原文 ===')
bad = 0
for it in items:
    if not it['gold_pages']: continue
    txt = ' '.join(b.text for g in it['gold_pages']
                   for b in doc(g['source_file'])[1][g['page_index']].blocks).lower()
    toks = set(re.findall(r'[A-Za-z]{3,}', it['question'].lower()))
    ov = sorted(t for t in toks if t in txt)
    if ov: print(f"  {it['id']} 重合: {ov}"); bad += 1
print(f"  有英文词重合的条目: {bad}/30")

print('\n=== 结论 ===')
if errs:
    print('发现问题:'); [print('  ✗', e) for e in errs]; sys.exit(1)
print('  全部通过：gold 页均存在、无 15.316、无受限页、标签与页面事实一致')
