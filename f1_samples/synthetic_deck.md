<!--
自写合成内容，非第三方课件，用于 F1 样例。

作者：本项目（2026-09-07 初版 503 词 / 2026-09-09 扩写为 10 页）。
不含任何第三方课件原文或其改写。PRD §12.1：对外展示一律使用自写的合成课件。

## 主题：Bloom filter（布隆过滤器）

刻意避开 22.081J（能源/核工程）与 14.129（博弈论/机制设计）。

## 2026-09-09 为什么扩写

初版 503 词整份喂进 user turn，导致 16 个样例里 11 个以「这份课件片段里没有
展开 counting variant……需要额外材料」收尾——**免责全部指向同一处前向引用**：
课件提到「删除需要计数变体」却没解释它。模型按生产 system 的 X06 立场
（「片段不够回答就说清楚缺什么」）如实说了出来。

扩写的目的**不是靠长度**，是让「只喂一部分」在结构上合法：
那句前向引用现在落在第 8 页并在那里被真正解释，而第 8 页不喂。

## 页与块

每页 2–3 个**块**，每块 40–60 词（≈250–380 字符），对齐实测真实切片
（n=450：字数中位 271、范围 60–433；每轮恒喂 5 个）。块 id 写在注释里，
`generate_pairs.py` 按 id 取用，不靠行号或正则去猜。

## 喂哪 5 块（口径 F28：我在扮演检索器）

`p1b1` 问题 · `p2b1` 结构与两个操作 · `p4b1` 无假阴性 ·
`p5b2` 误判率四步推导 · `p7b1` 两处真实应用

**FED 块的两条准入条件**（换素材时逐块过一遍）：

1. **页间无前向引用**——不提任何只在不喂的页上解释的东西。
   counting variant / cuckoo filter / 定 m,k 的算例全部在不喂的页上。
2. **块内无悬空前提**——不承接未喂块的假设、指代或符号。
   2026-09-09 实测踩到：p5b2 原文以「Assume the hash functions…」起句，
   假设承接的是不喂的 p5b1；且 `n` 在全部 5 个 FED 块里从未定义过
   （`m`/`k` 在 p2b1 有定义，`n` 没有）。已改为让假设与三个符号在块内自足。
   这两条是同一类，只是引用对象一个在页间、一个在块内——
   而后者正是上一轮候选列表里没有的那一项（口径 F26）。

## 四条轴的着力点（换素材时照这节判断新素材够不够格）

| 轴 | 着力点（且必须落在被喂的 5 块里） |
| --- | --- |
| 详细程度 | 五块内容可一句带过也可逐块展开，短讲长讲两端都成立 |
| 深度 | `what` 停在 p2b1；`why` 要用 p4b1 解释「为什么不漏却会误判」；`derivation` 有 p5b2 的四步式子，独立性假设显式 |
| 结构 | p4b1 的「永不漏报」是可前可后的结论 |
| 举例 | p7b1 给了浏览器与存储引擎两个真实用例，`fewer` 那侧删掉讲解仍完整 |
-->

# Lecture 7 — Bloom Filters

**CS 2xx · Probabilistic Data Structures**

---

## Page 1 — The problem

<!-- p1b1 · FED -->
A service must answer one question millions of times a second: have I seen this
item before? A hash set answers exactly but stores every item. At a billion URLs the
memory bill dominates the design, so it is worth asking whether an exact answer is
required at all.

<!-- p1b2 -->
Many systems only need a cheap filter in front of an expensive check. If a structure
can say "definitely not present" quickly and cheaply, the expensive check runs only
on the remainder. Being occasionally wrong in one direction is acceptable; being
wrong in the other direction is not.

---

## Page 2 — The structure

<!-- p2b1 · FED -->
A Bloom filter is an array of `m` bits, all initially zero, plus `k` hash functions
mapping an item to `k` positions. To insert `x`, set all `k` of its bits to one. To
test `y`, read its `k` positions: any zero means `y` was never inserted; all ones
means `y` is probably present.

<!-- p2b2 -->
The filter stores no items, only bits. This is what buys the space saving: memory
depends on `m` and not on how long the stored items are. A billion 60-byte URLs
occupy gigabytes in a hash set and a few hundred megabytes in a filter sized for a
one-percent error rate.

---

## Page 3 — The two operations in detail

<!-- p3b1 -->
Insertion never clears a bit and never needs to read one first, so inserts are
independent of each other and can be applied in any order. Two filters built over
the same `m` and `k` can be merged with a bitwise OR, which is why filters compose
well across shards.

<!-- p3b2 -->
A query touches exactly `k` bit positions regardless of how many items the filter
holds. Query cost is therefore constant, not logarithmic. On modern hardware the
dominant cost is the `k` cache misses, which is one reason small `k` is often
preferred in practice even when a larger `k` looks better on paper.

---

## Page 4 — Why there are no false negatives

<!-- p4b1 · FED -->
Bits are only ever turned on. If `y` was inserted, all `k` of its bits were set then
and nothing could have cleared them, so "not present" is always correct. "Present"
can be wrong: those `k` bits may have been set by other items. That is a false
positive, and it is the only error the structure makes.

<!-- p4b2 -->
The asymmetry is what makes the structure usable. A system can act on "not present"
immediately and treat "present" as a hint that triggers the expensive exact check.
Designs that need the opposite guarantee — never claim absence wrongly — cannot use
this structure at all.

---

## Page 5 — The false positive rate

<!-- p5b1 -->
The rate depends on three numbers: the array size `m`, the number of hash functions
`k`, and the number of items inserted `n`. It does not depend on the items
themselves, provided the hash functions spread them evenly. This is what makes the
rate something you can size for in advance rather than measure afterwards.

<!-- p5b2 · FED -->
The standard derivation assumes the `k` hash functions spread items uniformly and
independently. Insert `n` items into the `m`-bit array: one hash leaves a bit at zero
with probability `1 - 1/m`, so across `kn` hashes it stays zero with probability
about `e^(-kn/m)`. So a bit is one with probability `1 - e^(-kn/m)`, and a
non-member reads all ones with probability about `(1 - e^(-kn/m))^k`.

<!-- p5b3 -->
The independence assumption in the last step is not exactly true, because the `k`
positions are drawn from the same array and are therefore weakly correlated. The
approximation is close enough for sizing, and the exact treatment changes the third
decimal place rather than the engineering decision.

---

## Page 6 — Choosing k

<!-- p6b1 -->
More hash functions set more bits per insert and fill the array faster; fewer give
each query less evidence to work with. Differentiating the expression above with
respect to `k` gives an optimum at `k = (m/n) ln 2`, at which point roughly half the
bits in the array are ones.

<!-- p6b2 -->
Half-full is a useful sanity check when debugging a deployment. A filter whose bit
array is nearly all ones is overloaded and its false positive rate has collapsed
toward one; a filter that is nearly all zeros is oversized and wasting memory that
some other part of the system could use.

---

## Page 7 — Where this is used

<!-- p7b1 · FED -->
Browsers screen URLs against a local filter before paying for a network lookup
against the full malicious-site list, so a safe URL costs no round trip. Storage
engines keep a filter per data file so a read can skip files that certainly do not
hold the key.

<!-- p7b2 -->
Both cases share a shape: a cheap probabilistic no, followed by an expensive
authoritative yes. When the expensive check is unavailable — offline, rate limited,
or simply too slow for the request budget — the filter's answer has to stand alone,
and a false positive becomes a user-visible error rather than a wasted lookup.

---

## Page 8 — Deletion and counting filters

<!-- p8b1 -->
A standard Bloom filter cannot delete. Clearing the `k` bits of a removed item would
also clear bits that other items rely on, and any of those items would then be
reported absent — destroying the no-false-negatives guarantee that made the
structure worth using.

<!-- p8b2 -->
A counting Bloom filter replaces each bit with a small counter, typically four bits.
Insertion increments the `k` counters, deletion decrements them, and a query asks
whether all `k` counters are non-zero. The guarantee survives deletion because a
counter only reaches zero when every item that set it has been removed.

<!-- p8b3 -->
The cost is memory: four-bit counters make the structure four times larger for the
same `m`. Counters can also saturate under heavy insertion, and a saturated counter
can never be safely decremented again, so deployments either size generously or
accept a slow drift toward stale entries.

---

## Page 9 — Sizing: a worked example

<!-- p9b1 -->
Suppose `n` is ten million items and the target false positive rate is one percent.
Solving the rate expression for `m` gives roughly `m = -n ln(p) / (ln 2)^2`, which
here is about 96 million bits, or 12 megabytes. The optimal `k` follows as about
seven hash functions.

<!-- p9b2 -->
Tightening the target to one in a thousand costs another 50 percent of memory and
raises `k` to about ten. The relationship is logarithmic in `p`, so each additional
decimal place of accuracy costs a fixed increment of memory rather than a
multiplicative one.

<!-- p9b3 -->
In practice `n` is an estimate and the filter is sized for the upper end of it.
Exceeding the assumed `n` does not fail loudly; the rate simply degrades, which is
why production deployments monitor the fraction of set bits rather than waiting for
complaints about wrong answers.

---

## Page 10 — When not to use one

<!-- p10b1 -->
A cuckoo filter supports deletion without counters and often uses less space at low
error rates, at the cost of a more complex insert path that can fail and require a
rebuild. Choose it when deletion matters and insert latency has slack.

<!-- p10b2 -->
When the item set is small enough to fit in memory exactly, use an exact set: the
filter's whole value is trading accuracy for space, and there is nothing to trade
when space is not the binding constraint.
