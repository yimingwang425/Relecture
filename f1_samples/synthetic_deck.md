<!--
自写合成内容，非第三方课件，用于 F1 样例。

作者：本项目（2026-09-09）。不含任何第三方课件原文或其改写。
PRD §12.1：对外展示一律使用自写的合成课件——F1 样例给所有用户看，等于对外展示。

## 主题：Bloom filter（布隆过滤器）

刻意避开 22.081J（能源 / 核工程）与 14.129（博弈论 / 机制设计）。F1 样例与
F2/F3 测试集若是同一份材料，以后讲解质量出问题就分不清是样例设计的锅还是课件特性的锅。

## 为什么这个主题同时撑得住四条轴

换素材时用这一节判断新素材够不够格。**四条都要成立，缺一条那条轴的样例对就没有差异可造。**

| 轴 | 本材料里的着力点 |
| --- | --- |
| **详细程度**（P0–P3） | 有六个可独立展开也可一句带过的层次：问题 → 结构 → 两个操作 → 无假阴性 → 误判率 → 参数选择。短讲取前三层，长讲六层全开，**两端都成立**而不是长的那端靠注水 |
| **深度**（what / why / derivation） | 概念带**机制**而不只是定义：`what` 停在「概率型集合结构」；`why` 要解释「为什么不会漏、却会误判」；`derivation` 有一条四步可推的误判率式子（独立性假设是显式的，可被讨论），**三档各有实质内容可讲** |
| **结构**（先结论 / 先铺垫） | 有一个**可前可后**的结论：「用可调的小误判率换大幅空间节省，且永不漏报」。先说它再解释机制成立，先铺垫问题与结构再收到它也成立 |
| **举例**（more / fewer） | 有天然的类比（共用挂钩的衣帽间）与真实用例（浏览器恶意网址库、数据库读放大），**可举可不举**——`fewer` 那侧删掉例子讲解仍然完整 |

英文撰写：`source_language='en'` / `explain_language='zh'`。中文课件会让
`chinese_ratio` 维度不激活，那一维在核对脚本里就是空转的（通则七 #5）。
-->

# Lecture 7 — Bloom Filters

**CS 2xx · Probabilistic Data Structures**

## The problem

Suppose a service must answer one question, millions of times a second: *have I seen
this item before?* A hash set answers it exactly, but it stores every item it has
seen. When the set holds a billion URLs, the memory bill dominates the design. The
question worth asking is whether we actually need an exact answer.

## The structure

A Bloom filter is an array of `m` bits, all initially zero, together with `k`
independent hash functions. Each hash function maps an item to one of the `m`
positions. The filter stores no items — only bits.

**Insert.** To add an item `x`, compute the `k` hash values and set all `k`
corresponding bits to one. Bits that are already one stay one; nothing is ever
cleared.

**Query.** To test whether `y` is present, compute the same `k` positions. If any
one of them is zero, `y` was definitely never inserted. If all `k` are one, report
that `y` is probably present.

## Why there are no false negatives

The asymmetry follows from the fact that bits are only ever turned on. If `y` was
inserted, all `k` of its bits were set at that moment, and no later operation could
have turned any of them back off. So a "not present" answer is always correct. A
"present" answer can be wrong: the `k` bits may have been set by a combination of
other items that happens to cover exactly those positions. This is a *false
positive*, and it is the only kind of error the structure makes.

## How likely is a false positive?

Assume the hash functions distribute items uniformly and independently. After
inserting `n` items:

1. A single bit is left at zero by one hash of one item with probability `1 - 1/m`.
2. Across `kn` hashes, it stays zero with probability `(1 - 1/m)^(kn)`, which for
   large `m` is approximately `e^(-kn/m)`.
3. So a given bit is one with probability `1 - e^(-kn/m)`.
4. A non-member is reported present when all `k` of its positions are one, which
   happens with probability approximately `(1 - e^(-kn/m))^k`.

The independence assumption in step 4 is not exactly true — the `k` positions are
drawn from the same array — but the approximation is close enough for sizing.

## Choosing the number of hash functions

More hash functions set more bits per insert, which fills the array faster; fewer
give each query less evidence to work with. Differentiating the expression above
gives an optimum at `k = (m/n) ln 2`, where the array is about half ones.

## Where this is used

Browsers screen URLs against a local filter before paying for a network lookup of
the full blocklist. Storage engines keep a filter per data file so a read can skip
files that certainly do not hold the key.

## The limitation

Items cannot be removed: clearing a bit would break the no-false-negatives
guarantee for every other item sharing it. Deletions require a counting variant.
