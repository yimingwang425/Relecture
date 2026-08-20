# 14.129 判据验证（20 条候选，不建完整表）

来源：`实验脚本/测试集/MIT14_129S25_lec02.pdf`（Lecture 2: Blockchain as a Database，50 页）
目的：验证 `glossary_criteria.md` 的五条判据是否在「日常词被赋予精确技术义」的学科里成立
决策日期：2026-08-19

## 结论先说

**G2 没有崩，但它单独使用会崩掉这门课。**

判据本身是对的——它准确地拒绝了 `block` `token` `consensus` `velocity` `liability`。
问题在于：在 22.081J，G2 拒绝的是边缘词（`conservation` `insulation`）；
在 14.129，**G2 拒绝的是这门课的核心概念**。没有 `block` 和 `token` 就没法讲区块链。

而这类学科恰恰是最需要术语标注的——学生看到「区块」时最容易以为自己已经懂了。
一个把核心概念全部排除的判据，在这里等于关掉了整个维度。

**修订方案：G2 拆成英文侧与中文侧，任一侧无歧义即可收录。**

理由：审查对象是**中文讲解文本**，不是英文课件。匹配主要发生在中文侧。
`block` 的英文裸形有歧义，但中文「区块」没有——它足以做匹配锚点。
英文裸形歧义 ≠ 不可匹配，原判据把这两件事混为一谈了。

10 个触发 G2 的词里，**4 个可以直接靠中文锚点救回**，3 个靠复合形式解决，
2 个实际卡在 G3 而非 G2，1 个确实该排除。

---

## 一、判据通过的 20 条候选

`anchor` 列表示用哪一侧做匹配锚点。

| # | 英文 | 中文（首项为首选） | anchor | 备注 |
| --- | --- | --- | --- | --- |
| 1 | blockchain | 区块链 | both | |
| 2 | distributed ledger | 分布式账本 | both | 缩写 DLT 入 aliases |
| 3 | genesis block | 创世区块 | both | R1 复合形式 |
| 4 | nonce | 随机数, 一次性随机数 | **en** | 中文「随机数」过泛，不能做锚点 |
| 5 | proof of work | 工作量证明 | both | 缩写 POW |
| 6 | hash | 哈希, 散列 | both | |
| 7 | state machine | 状态机 | both | |
| 8 | CAP theorem | CAP 定理 | both | |
| 9 | partition tolerance | 分区容错 | both | |
| 10 | Byzantine fault tolerant | 拜占庭容错 | both | |
| 11 | Sybil resistant | 抗女巫攻击 | both | |
| 12 | finality | 终局性 | both | p42「time to finality」 |
| 13 | throughput | 吞吐量 | both | |
| 14 | central bank digital currency | 央行数字货币 | both | 缩写 CBDC |
| 15 | real-time gross settlement | 实时全额结算 | both | 缩写 RTGS |
| 16 | medium of exchange | 交换媒介 | both | |
| 17 | store of value | 价值储藏 | both | 裸形 `store` 有歧义，复合形式无 |
| 18 | promissory note | 本票, 期票 | both | |
| 19 | mutual credit network | 互助信用网络 | both | |
| 20 | community inclusion currency | 社区包容性货币 | both | 缩写 CIC |

## 二、触发 G2 的 10 条 —— 修订判据后的处置

| 词 | 英文侧冲突证据 | 中文形 | 中文侧 | 处置 |
| --- | --- | --- | --- | --- |
| `block` | **同一文档内**：p4「signed blocks」（技术义）vs p20「the basic building block」（日常义） | 区块 | ✓ 无歧义 | **收**，anchor=zh |
| `token` | **同一文档内**：p41「the tokens themselves」（技术义）vs p20「an actual portable physical token」（日常义） | 代币 | ✓ 无歧义 | **收**，anchor=zh |
| `velocity` | p17 显式定义为「amount traded per unit time divided by stock outstanding」，但裸形日常义是「速度」 | 流通速度 | ✓ 无歧义 | **收**，anchor=zh |
| `liability` | 负债 vs 责任/义务；p26「money as a liability to the community」 | 负债 | ✓ 无歧义 | **收**，anchor=zh |
| `silo` | p8「separate silos」；裸形日常义是筒仓 | 数据孤岛 | 需复合 | 收 `data silo`，裸形入 unsafe |
| `consensus` | p20「Consensus financial accounts」vs p41「consensus validation」 | 共识 | ✗ 中文亦为日常词 | 收 `consensus mechanism` / `consensus validation`，裸形不收 |
| `chain` | 裸形歧义极强 | 链 | ✗ | 只收 `blockchain`，裸形不收 |
| `ledger` | 英文侧无冲突 | 账本 | — | **卡在 G3 不是 G2**：账本是中文日常词。只收 `distributed ledger` |
| `node` | 英文侧尚可 | 节点 | — | **卡在 G3**：节点是中文常用词。不收 |
| `trust` | — | 信任 | ✗ | C 档，排除 |

净结果：10 条里 **6 条被救回**（4 条靠中文锚点、2 条靠复合形式），2 条实际是 G3 问题，
2 条确实该排除。修订前这 10 条全部丢失。

---

## 三、判据修订项

以下三条已写入 `glossary_criteria.md`：

1. **G2 拆分为 G2-en / G2-zh，任一侧通过即可收录**，并在条目上记录 `match_anchor`
   （`en` / `zh` / `both`），指明该条用哪一侧做匹配锚点。
2. **anchor 不是可选优化，是正确性前提。** 若 `block` 用英文锚点，模型输出里
   「基本构成单元（building block）」会被错误计入分母，直接压低标注率——
   这是误收造成误判，正是 PRD §8.1 说的危险方向。
3. `unsafe_aliases` 的语义相应细化为「该形式在**指定锚点侧**不可用于匹配」，
   而非笼统的「不可用」。

## 四、对 22.081J 的回溯影响

按修订后判据重跑 22.081J，**改动很小**：B1 档（一词多义）的 13 条里，
只有 `thermal equilibrium` 一条因中文「热平衡」无歧义而具备收录资格，
其余（`conservation` `insulation` `reserves` `footprint` `generator` `peak`
`shaft` `efficiency` `flow rate` …）虽然中文侧也无歧义，但**全部卡在 G3**——
节能、保温、储量、效率都是中文媒体日常词。

这个对比本身是结论：

> **判据的脆弱性是学科相关的。**
> 能源课的术语以复合名词短语为主，天然不与日常词碰撞，G2 几乎不触发；
> 金融/CS 课把日常词赋予精确技术义，G2 会在核心概念上密集触发。
> 只用一门课验证判据必然过拟合——这次跑 14.129 的价值就在这里。

因此 22.081J 的术语表**不需要因判据修订而重建**，只需按本轮新增的
`match_anchor` 字段补标。

## 五、下一步

11.201（城市规划）的形态与这两门都不同：术语多为「制度/流派名称」
（`comprehensive planning` `advocacy planning` `rational model`），
预计主要压力在 G5（专有名词边界）而非 G2——「某某规划流派」算不算专有名词，
判据目前没说清。建议下一轮验证 G5。
