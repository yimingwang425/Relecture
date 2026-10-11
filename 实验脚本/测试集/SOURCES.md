# 测试集出处

本目录存放评测与实验用的课件样本。**只收公开授权材料**，同学课件一律不入库
（PRD §12.1：课件仅存于用户个人空间，不建公共库）。

新增文件必须同时在此表登记出处、许可与下载日期。无出处记录的文件视为不合规，
应从版本库移除。

## 使用范围（硬约束）

> **OCW 材料只用作开发与实验的输入。**
> 对外展示——demo 截图、简历、面试材料、博客、演示视频——**一律使用自写的
> 合成课件**，不得出现 OCW 原文或基于 OCW 原文生成的讲解内容。

理由：CC BY-NC-SA 的 SA（相同方式共享）条款约束改编作品。模型基于 OCW 课件
生成的讲解文本是否构成改编，法律上没有确定答案；内部评测不触发分发，一旦对外
展示就进入了这个灰区。与其逐案论证，不如在源头切断——**合成课件的成本远低于
一次授权争议的成本**，且合成课件还有额外好处：可以按需构造极端样本（超长引用、
纯英文段落、术语密集段）来测审查 agent，真实课件反而不好造。

配套要求：`examples/` 下用于 README 与对外文档的样例文本必须是自写合成的，
不得从本目录的 PDF 中摘录。

**仓库公开后的处置（2026-10-11）。** 这些材料最初按「内部使用」入库，前提是
仓库为 private。仓库改为 public 等于对外分发，于是按材料分别处理：

| 材料 | 处置 |
| --- | --- |
| OCW 原文 PDF（三份整份 + 14.129） | 保留。CC BY-NC-SA 允许署名后非商业分发；署名见下文「署名」一节 |
| 14.129 的 8 页第三方保留权利内容 | **不随仓库分发**：入库副本里这 8 页已置空，见下文「⚠️ 14.129」一节 |
| 基于 OCW 课件生成的讲解（`真实样本/`、`explain/runs/agent_live_*`） | 保留，**按 CC BY-NC-SA 发布**。它算不算 SA 条款下的改编作品没有定论——两种情况都按改编作品处理：沿用同一许可，问题就不存在了 |

许可证声明见同目录 [`LICENSE.md`](LICENSE.md)。漂移实验的 `runs/` 已默认 gitignore，
不受此影响。

上面「使用范围」那条规则**照旧**：它管的是 demo 截图、简历、面试材料这类**对外展示**，
那里仍然只用合成课件。仓库公开分发的是评测材料本身，二者不是一回事。

## 文件登记

| 文件 | 课程 | 学科 | 内容 | 来源 | 许可 | 下载日期 |
| --- | --- | --- | --- | --- | --- | --- |
| `MIT22_081JF10_lec01b.pdf` | 22.081J *Introduction to Sustainable Energy*, Fall 2010 | 能源工程 | Lecture 1b: Overview of energy use and related issues（39 页） | [课程页](https://ocw.mit.edu/courses/22-081j-introduction-to-sustainable-energy-fall-2010/) · [PDF](https://ocw.mit.edu/courses/22-081j-introduction-to-sustainable-energy-fall-2010/a0eac4cd3f655795dad62021b1a4c769_MIT22_081JF10_lec01b.pdf) | CC BY-NC-SA | 2026-08-19 |
| `MIT11_201F07_slides2.pdf` | 11.201 *Gateway: Planning Action*, Fall 2007 | 城市规划 / 公共政策 | Planning History and Traditions: Overview（15 页） | [课程页](https://ocw.mit.edu/courses/11-201-gateway-planning-action-fall-2007/) · [PDF](https://ocw.mit.edu/courses/11-201-gateway-planning-action-fall-2007/5457ed17ee1ba51263c309c8e20ec2c0_slides2.pdf) | CC BY-NC-SA | 2026-08-19 |
| `MIT15_316SU05_leader_dev.pdf` | 15.316 *Building and Leading Effective Teams*, Summer 2005 | 管理 / 领导力 | How do you develop leadership?（19 页） | [课程页](https://ocw.mit.edu/courses/15-316-building-and-leading-effective-teams-summer-2005/) · [PDF](https://ocw.mit.edu/courses/15-316-building-and-leading-effective-teams-summer-2005/c5a0d8dea9e3b0e77777f4358691f907_leader_dev.pdf) | CC BY-NC-SA | 2026-08-19 |
| `MIT14_129S25_lec02.pdf` | 14.129 *Blockchain and the Design of Financial Systems*, Spring 2025 | 金融 / 计算机 | Lecture 2: Blockchain as a Database（50 页，**其中 8 页已置空**） | [课程页](https://ocw.mit.edu/courses/14-129-blockchain-and-the-design-of-financial-systems-spring-2025/) · [PDF](https://ocw.mit.edu/courses/14-129-blockchain-and-the-design-of-financial-systems-spring-2025/mit14_129_s25_lec02.pdf) | CC BY-NC-SA；**已修改，见下** | 2026-08-19 |

文件已按 MIT 命名惯例重命名（原始 OCW 文件名带哈希前缀，不具可读性）；
原始直链保留在上表中，出处不丢。

## 署名

> MIT OpenCourseWare. License: Creative Commons BY-NC-SA. https://ocw.mit.edu
>
> - 22.081J *Introduction to Sustainable Energy*, Fall 2010.
> - 11.201 *Gateway: Planning Action*, Fall 2007.
> - 15.316 *Building and Leading Effective Teams*, Summer 2005.
> - 14.129 *Blockchain and the Design of Financial Systems*, Spring 2025.
>   Changes: pages 10, 11, 25, 26, 31, 38, 40 and 42 were replaced with blank pages
>   (third-party content excluded from the CC license).

CC BY-NC-SA 的三项义务对本项目的含义：

- **BY（署名）**：即本文件。改动也要标出来——14.129 的置空写在上面的署名里。
- **NC（非商业）**：仅用于本项目的评测与实验。这与 PRD §3.3「v1 不追求盈利与
  商业化验证」一致；若产品后续商业化，测试集需重新审视。
- **SA（相同方式共享）**：约束改编作品，是「使用范围」一节的直接来源。

## ⚠️ 14.129 含第三方保留权利内容——入库副本已置空这 8 页

**OCW 课件不必然整份都是 CC 授权的。** 14.129 这份 50 页的讲义里有 **8 页**
（第 10、11、25、26、31、38、40、42 页）明确标注：

> © … All rights reserved. This content is excluded from our Creative Commons
> license. For more information, see https://ocw.mit.edu/help/faq-fair-use/

涉及 6 个权利人：Cambridge University Press、Federal Reserve Bank of Boston、
Federal Reserve Bank of Kansas City、Goldstein et al.、Legare Street Press，
以及一条 **© source unknown**（最麻烦的一种——想取得授权都不知道找谁）。

另外三份经检查为 0 页。

**处置（2026-10-11，仓库公开前）**：这 8 页在入库副本里**换成同尺寸的空白页**，
其余 42 页原样保留。为什么是置空、不是删页：删页会让后面每一页的页号前移，
检索评测集、parse-service 的 golden、切片测试里写死的页号（p33–36 的
image_only 段等）会全部错位；置空则页号不变，这 8 页解析成 `empty` 被跳过。
`retrieval/retrieval/corpus.py` 的 `RESTRICTED_PAGES` 仍保留这 8 页，作为第二道排除。

⚠️ **git 历史里的旧提交仍含原版。** 置空只管今后的版本；公开前必须新建仓库或
改写历史，否则原版 PDF 仍能从历史里取出来。

**这条经验要带进产品**：F2 的课件处理流程应当识别并记录此类版权声明。
用户上传的教材同样可能夹带第三方保留权利的图表，PRD §12.1 的上传确认文案
（「我确认这是我本人所修课程的材料」）覆盖不到这一层——学生自己也不知道
老师的 slides 里哪张图是从别处扒的。

## 各样本的技术特征

用 `/Subtype /Image` 的尺寸与页面 MediaBox 比对判定，非人工翻阅。

| 文件 | 内嵌字体 | 图像 | 文字绘制算符 | 判定 |
| --- | --- | --- | --- | --- |
| `MIT22_081JF10_lec01b.pdf` | 12 | 20（插图） | 2549 | 文字型（LaTeX beamer） |
| `MIT11_201F07_slides2.pdf` | 1 | 2 | 271 | 文字型，图少、纯文字幻灯片 |
| `MIT15_316SU05_leader_dev.pdf` | **0** | 22，**每页一张全页图**（3134×2271 铺满 805×621 版面） | 589 | **扫描件 + OCR 文本层** |
| `MIT14_129S25_lec02.pdf` | 0 | 228（每页 4–5 张局部插图） | 1431 | 文字型，图表密集（数字测于置空前的原版） |

### 15.316 是 bad case 样本，不是术语提取样本

它正好命中 PRD §6.2 与 §10 的「扫描件 / 图片型课件」边界，价值在于此，
不应作为术语表提取或讲解质量评测的输入——OCR 文本层已经有明显噪声
（`developleadership` 缺空格、项目符号被识别成 `*` 和 `:`）。

由此得到一条对 F2 的设计要求：**扫描件检测不能用「能否抽出文本」作判据**。
本文件能抽出 589 个文字算符、19 页共约数千字符，任何「有文本即通过」的检查都会
放行它，然后把 OCR 噪声一路带进切片、术语表和讲解。检测判据应当是
**内嵌字体数 ≈ 0 且存在铺满版面的全页图像**——这两个信号在本文件上都成立，
而在其余三份上都不成立。
