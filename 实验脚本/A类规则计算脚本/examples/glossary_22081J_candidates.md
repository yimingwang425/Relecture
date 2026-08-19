# 22.081J Lecture 1b · 术语候选分类

来源：`实验脚本/测试集/MIT22_081JF10_lec01b.pdf`（39 页，全文逐页人工过一遍）
判据：`glossary_criteria.md`
决策日期：2026-08-19

## 结论摘要

| 档 | 条数 | 处理 |
| --- | --- | --- |
| A-1 · 明确专业术语（正文） | **27** | 收录，`source: body_text` |
| A-2 · 明确专业术语（仅见于图注） | **17** | 收录，`source: figure_caption` |
| B · 边界模糊 | 27 | 不收，备查 |
| C · 明确普通词 | 约 90 | 不收，作为提取策略的负例集 |

共 44 条，`source` 字段区分两类，一份数据可算两种口径。

**两个数必须分开报告，它们不是一回事：**

| 数 | 含义 | 本课取值 |
| --- | --- | --- |
| 术语表相对课件的**覆盖缺口** | 自动提取能从课件里捞到多少人工表条目 | 图注 17 条现有策略抓不到，覆盖率 61% |
| 讲解文本相对术语表的**分母缺口** | 模型输出里实际出现了多少术语 | **不等于上一行**——图注术语正文未出现，模型讲解时大概率也不会提，本就进不了分母 |

最初把这两个数混为一谈，得出「漏 37% 会削弱指标」的结论，是错的。

---

## A-1 · 明确专业术语（正文，27 条 → 收录）

| # | 英文 | 中文 | 首现页 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | energy carrier | 能量载体 | p18 | 课件有准定义句「it's an energy carrier」 |
| 2 | energy density | 能量密度 | p18 | |
| 3 | power density | 功率密度 | p21 | 全篇复现 4 次，本课核心量 |
| 4 | heat engine | 热机 | p20 | |
| 5 | hydraulic head | 水力压头 | p29 | |
| 6 | nuclear fission | 核裂变 | p17 | |
| 7 | nuclear fusion | 核聚变 | p17 | |
| 8 | light water reactor | 轻水堆 | p17 | alias: LWR |
| 9 | breeder reactor | 增殖堆 | p17 | 课件作 `breeders`；`breeder` 单形有日常义，按 R1 标 unsafe |
| 10 | uranium enrichment | 铀浓缩 | p26 | 按 R1 取复合形式；`enrichment` 单形不匹配 |
| 11 | fuel rod | 燃料棒 | p26 | |
| 12 | reactor vessel | 反应堆压力容器 | p26 | 正文与图注均出现 |
| 13 | fission product | 裂变产物 | p28 | |
| 14 | carbon sequestration | 碳封存 | p23 | alias: sequester CO₂ |
| 15 | solar irradiance | 太阳辐照度 | p34 | |
| 16 | solar constant | 太阳常数 | p34 | 课件有显式定义标记「known as the」 |
| 17 | solar thermal | 光热发电 | p19 | |
| 18 | photovoltaic | 光伏 | p19 | **课件写 `solar voltaic`（非标准）**，按 R2 以标准写法为主形 |
| 19 | biomass | 生物质 | p19 | |
| 20 | oil shale | 油页岩 | p4 | |
| 21 | tar sands | 油砂 | p4 | alias: oil sands |
| 22 | waste heat | 废热 | p20 | |
| 23 | exhaust steam | 乏汽 | p20 | |
| 24 | natural uranium | 天然铀 | p26 | |
| 25 | DT reaction | 氘氚反应 | p17 | alias: D-T reaction |
| 26 | DD reaction | 氘氘反应 | p17 | alias: D-D reaction |
| 27 | DC electricity | 直流电 | p34 | alias: direct current |

## A-2 · 明确专业术语（仅见于图注，17 条 → 收录，标 `source: figure_caption`）

全部来自两张示意图：p20 火电厂流程图、p27 压水堆结构图。**正文一次都没提过。**

| 英文 | 中文 | 出处 |
| --- | --- | --- |
| heat exchanger | 换热器 | p20 图 |
| condenser | 冷凝器 | p20 / p27 图 |
| steam turbine | 汽轮机 | p20 图 |
| furnace | 炉膛 | p20 图 |
| makeup water | 补给水 | p20 图 |
| containment structure | 安全壳 | p27 图 |
| primary loop | 一回路 | p27 图 |
| secondary loop | 二回路 | p27 图 |
| cooling loop | 冷却回路 | p27 图 |
| steam generator | 蒸汽发生器 | p27 图 |
| alternator | 交流发电机 | p27 图 |
| cooling tower | 冷却塔 | p27 图 |
| reactor core | 堆芯 | p27 图 |
| control rod | 控制棒 | p27 图 |
| pressurizer | 稳压器 | p27 图 |
| coolant | 冷却剂 | p27 图 |
| turbine | 涡轮机 | p27 图 |

### 这 17 条是本次分类最重要的发现

它们全部通过五条判据——是货真价实的硬术语，且中文译名固定、匹配无歧义。
唯一的问题是**出现位置**。

PRD §7.1 的提取策略是「加粗/标题/定义框词汇、跨页高频名词短语、Glossary 页」。
这 16 条**一条都抓不到**：图注文字没有加粗、不是标题、不在定义框，且每条只出现
一次，跨页高频完全失效。

也就是说：**自动提取相对人工表的召回率上限是 61%**，缺的 39% 全在图注。

三点后续影响：

1. **这是覆盖缺口，不是分母缺口。** 图注术语正文从未出现，模型讲解时大概率也
   不会提，本就进不了 `term_annotation_rate` 的分母。两个数分开报告，
   `source` 字段就是为此存在。
2. **召回率是独立指标。** 人工表是金标准，自动提取的召回率决定
   `term_annotation_rate` 的可信度——与 PRD §8.5「先校准 judge 再画漂移曲线」
   同理，先验证测量工具。
3. **图注文字在 PDF 文本层里是拿得到的**（本次分类就是从文本层抽出来的），
   漏掉的原因不是抽不出，是**没有结构信号可依赖**。若要补齐，需要一条独立通道：
   识别「孤立的、无句法结构的名词短语簇」，而非沿用高频启发式。这条通道的
   误收风险高，建议配合 PRD 已设计的用户手动增删入口，而不是全自动。

---

## B · 边界模糊（27 条 → 不收，备查）

### B1 · 一词多义（13 条，违反 G2-en）

**判据修订后复查**：这 13 条的中文侧其实都无歧义（节能、保温、储量、效率……），
按 G2-zh 本可用中文锚点收录。但它们**全部卡在 G3**——这些中文词都是媒体日常词，
标注了也帮不到学生。所以结论不变，理由变了：不是「匹配不了」，是「不值得标注」。

唯一的例外是 `equilibrium`：中文「热平衡」既无歧义又非日常词，具备收录资格。
但本节 lecture 只写了裸形 `equilibrium`（p24），按 R1 需以 `thermal equilibrium`
形式收录，而该形式课件中并未出现——留在 B 档，待后续 lecture 出现时再收。


| 词 | 冲突义项 |
| --- | --- |
| conservation | 节能 / 能量守恒 / 自然保护 —— 三义并存，本课三种都可能出现 |
| insulation | 保温隔热 / 电绝缘 |
| reservoir | 水库 / 储层 |
| footprint | 占地面积 / 碳足迹 |
| generator | 发电机 / 生成器 |
| peak | 峰值 / 达峰 |
| shaft | 轴 / 竖井 |
| equilibrium | 热平衡 / 一般均衡 |
| reserves | 储量 / 储备 |
| enrichment（单形） | 铀浓缩 / 丰富充实 —— 已按 R1 以 `uranium enrichment` 收录 |
| breeder（单形） | 增殖堆 / 饲养者 —— 同上 |
| efficiency | η 的精确定义 / 日常「效率」 |
| flow rate | 技术量，但构词完全通用 |

### B2 · 中文媒体日常词，无需标注（14 条，违反 G3）

`global warming` `greenhouse effect` `greenhouse gas` `fossil fuel` `renewables`
`biofuel` `hydroelectricity` `geothermal` `natural gas` `power plant`
`plug-in hybrid` `standard of living` `carbon dioxide` `cost-benefit analysis`

### B3 · 译名不统一 —— 本档已撤销（2026-08-19 修订）

原先把 `turbine` `cost-benefit analysis` `sequester` 归入「译名不统一」档，
判断有误。**译名不统一不是分档问题，是条目结构缺一个字段。**

修订后按判据 R3：`zh` 的第一项为首选译名，其余为可接受变体，匹配时全部接受、
展示时只取第一项。`turbine` 因此写作 `["涡轮机", "透平"]` 并升入 A 档
（`source: figure_caption`）。

按判据 R4，首选译名取**通用**的那个而非更精确的那个——标注成「透平（turbine）」
对中文学生的帮助低于「涡轮机」。R4 的一般形式：

> 标注的价值取决于译名本身是否构成障碍。译名标准通用则标注有益；
> 译名比原文更生僻则标注有害。

原 B3 的其余两条改归他档：`cost-benefit analysis` → B2（成本效益分析是中文
媒体日常词，卡 G3）；`sequester` → `carbon sequestration` 的 `unsafe_alias`。
原列的 `grid` 系笔误，本节 lecture 全文未出现，已删。

---

## C · 明确普通词（约 90 条 → 提取策略的负例集）

自动提取若命中以下任何一条，说明策略过松，应回退。

**C1 · 高频普通名词（本课出现频次很高，最容易被词频启发式误收）**
`energy` `electricity` `heat` `water` `steam` `coal` `oil` `gas` `fuel` `power`
`wind` `solar` `sun` `earth` `temperature` `pressure` `radiation` `atmosphere`
`gravity` `pollution` `transportation` `heating` `cooling` `industry` `lighting`
`car` `house` `media` `public` `funds` `growth` `population` `supply` `production`
`technology` `reaction` `cost` `year` `data` `value` `source` `breakdown`
`problem` `issue` `solution` `option` `decision` `strategy` `reserves`

**C2 · 组合型伪术语（跨页高频名词短语，正是 PRD 现有策略最容易误收的一类）**
`energy use` `energy usage` `energy consumption` `energy sources` `fuel reserves`
`world energy consumption` `resource usage` `standard of living` `power output`
`energy situation` `proposed solutions` `major technologies`

C2 是对 PRD §7.1「跨页高频名词短语」这条启发式的直接反例：
`energy consumption` 在本课跨 4 页出现，词频信号极强，但它不是术语——
它是「能源消耗」，任何中文读者都懂，标注它纯属噪声。

**C3 · 专有名词（违反 G5）**
`MIT` `PSFC` `John C. Wright` `Cape Wind` `EIA-DoE` `doe.eia.gov`
`Wikimedia Commons` `Flickr` `Sandia National Labs` `Tennessee Valley Authority`
`USDA` `Saudi Arabia` `Russia` `China` `India` `Australia` `Quebec` `Shanghai`
`Bombay`

**C4 · 单位、化学式、符号（违反 G4）**
`Mtoe` `short ton` `barrel` `ft³` `tonne` `MWe` `MW` `kW/m²` `W/m²` `kWh/m³`
`CO₂` `CH₄` `C₈H₁₈` `H₂O` `O₂` `²³⁵U` `²³⁸U` `η` `ρgh` `Q` `T⁴` `β` `γ` `ν`

---

## 已决（2026-08-19）

1. **图注术语收录**，加 `source` 字段区分 `body_text` / `figure_caption`。
   原先担心的「§8.2 两个条件被混淆」不成立——有审查与无审查两组用同一份术语表，
   组成对两侧影响相同。加了 `source` 后一份数据可算两种口径，是否分开统计的决策
   推迟到有数据时再做。
2. **`turbine` 归 A 档**，见上文 B3 修订。
3. **判据已用 14.129 验证并修订**（G2 拆分为英文/中文双侧），见
   `glossary_14129_probe.md`。回溯检查表明 22.081J 的分类**不需要因此重建**，
   只需补 `match_anchor` 字段——本表 44 条全部为 `both`，这本身印证了
   「判据脆弱性是学科相关的」：能源术语以复合名词短语为主，不与日常词碰撞。
4. **11.201 待下一轮验证**，重点看 G5（专有名词边界）——规划学科的术语多为
   制度与流派名称（`advocacy planning` `rational model`），「某某规划流派」
   算不算专有名词，判据目前没说清。
