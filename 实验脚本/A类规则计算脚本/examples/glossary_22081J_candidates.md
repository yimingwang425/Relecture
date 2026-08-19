# 22.081J Lecture 1b · 术语候选分类

来源：`实验脚本/测试集/MIT22_081JF10_lec01b.pdf`（39 页，全文逐页人工过一遍）
判据：`glossary_criteria.md`
决策日期：2026-08-19

## 结论摘要

| 档 | 条数 | 处理 |
| --- | --- | --- |
| A-1 · 明确专业术语（正文） | **27** | 收录，即 `glossary_22081J.json` |
| A-2 · 明确专业术语（仅见于图注） | 16 | 登记，v1 不收（理由见下） |
| B · 边界模糊 | 30 | 不收，备查 |
| C · 明确普通词 | 约 90 | 不收，作为提取策略的负例集 |

正文术语 27 条，落在预估的 20–30 区间内。但这是**把图注术语剔出去之后**的数字——
不剔的话是 43 条。

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

## A-2 · 明确专业术语（仅见于图注，16 条 → v1 不收）

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

### 这 16 条是本次分类最重要的发现

它们全部通过五条判据——是货真价实的硬术语，且中文译名固定、匹配无歧义。
唯一的问题是**出现位置**。

PRD §7.1 的提取策略是「加粗/标题/定义框词汇、跨页高频名词短语、Glossary 页」。
这 16 条**一条都抓不到**：图注文字没有加粗、不是标题、不在定义框，且每条只出现
一次，跨页高频完全失效。

也就是说：**这节课 43 条硬术语中，PRD 现有策略会漏掉 37%。**

三点后续影响：

1. **误差方向仍然安全**（PRD §8.1 已论证）——漏收只降低灵敏度。所以 v1 可以先
   不收，不构成阻塞。
2. **但要知道漏了多少。** 「漏 37%」和「漏 5%」对指标可信度的影响完全不同。
   建议在 §8.2 漂移实验的报告里注明术语表覆盖率，否则术语维度的绝对值没有意义。
3. **图注文字在 PDF 文本层里是拿得到的**（本次分类就是从文本层抽出来的），
   漏掉的原因不是抽不出，是**没有结构信号可依赖**。若要补齐，需要一条独立通道：
   识别「孤立的、无句法结构的名词短语簇」，而非沿用高频启发式。这条通道的
   误收风险高，建议配合 PRD 已设计的用户手动增删入口，而不是全自动。

---

## B · 边界模糊（30 条 → 不收，备查）

### B1 · 一词多义，字符串匹配会误伤（违反 G2）

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

### B2 · 中文媒体日常词，无需标注（违反 G3）

`global warming` `greenhouse effect` `greenhouse gas` `fossil fuel` `renewables`
`biofuel` `hydroelectricity` `geothermal` `natural gas` `power plant`
`plug-in hybrid` `standard of living` `carbon dioxide`

### B3 · 译名不统一，标注反而可能加重困惑

| 词 | 并存译法 |
| --- | --- |
| turbine | 涡轮 / 透平 / 汽轮 |
| cost-benefit analysis | 成本效益分析 / 成本收益分析 |
| sequester | 封存 / 固定 / 螯合 |
| grid | 电网 / 网格 |

`turbine` 这一条值得单独说：它同时是「明确的专业术语」和「译名不统一」。
按 G2 它该收，按「标注的目的是帮助理解」它不该收——因为标注成「透平（turbine）」
对中文学生的帮助可能低于直接写「涡轮机」。这类词是 term_policy 三值配置真正的
决策点，也是最该交给用户手动决定的一类。

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

## 待你拍板

1. **A-2 的 16 条图注术语，v1 收不收？**
   我倾向不收（宁漏勿错，且提取阶段本就抓不到，收了会让「人工整理的表」和
   「自动提取的表」出现系统性差异，§8.2 的实验结论会不干净）。
   但如果你更看重指标灵敏度，收进去也成立——它们本身没有误收风险。

2. **`turbine` 这类「译名不统一」的词归 B 还是 A？**
   我放了 B。这类词是 term_policy 的真正决策点，建议留给用户手动加。

3. **另外三门课要不要用同一套判据先跑一遍？**
   11.201（城市规划）和 14.129（区块链金融）的术语形态与能源课差异很大，
   尤其 14.129 有大量「日常词被赋予精确技术义」的情况（`ledger` `block`
   `consensus` `finality`），G2 会被反复触发。跑完这两门再定判据，比只看
   一门课稳。15.316 是扫描件，不作为术语来源（见 SOURCES.md）。
