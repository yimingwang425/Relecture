# 测试集出处

本目录存放评测与实验用的课件样本。**只收公开授权材料**，同学课件一律不入库
（PRD §12.1：课件仅存于用户个人空间，不建公共库）。

新增文件必须同时在此表登记出处、许可与下载日期。无出处记录的文件视为不合规，
应从版本库移除。

## 使用范围（硬约束）

> **OCW 材料仅限内部开发与实验使用。**
> 对外展示——demo 截图、简历、面试材料、博客、演示视频——**一律使用自写的
> 合成课件**，不得出现 OCW 原文或基于 OCW 原文生成的讲解内容。

理由：CC BY-NC-SA 的 SA（相同方式共享）条款约束改编作品。模型基于 OCW 课件
生成的讲解文本是否构成改编，法律上没有确定答案；内部评测不触发分发，一旦对外
展示就进入了这个灰区。与其逐案论证，不如在源头切断——**合成课件的成本远低于
一次授权争议的成本**，且合成课件还有额外好处：可以按需构造极端样本（超长引用、
纯英文段落、术语密集段）来测审查 agent，真实课件反而不好造。

配套要求：`examples/` 下用于 README 与对外文档的样例文本必须是自写合成的，
不得从本目录的 PDF 中摘录。

## 文件登记

| 文件 | 课程 | 学科 | 内容 | 来源 | 许可 | 下载日期 |
| --- | --- | --- | --- | --- | --- | --- |
| `MIT22_081JF10_lec01b.pdf` | 22.081J *Introduction to Sustainable Energy*, Fall 2010 | 能源工程 | Lecture 1b: Overview of energy use and related issues（39 页） | [课程页](https://ocw.mit.edu/courses/22-081j-introduction-to-sustainable-energy-fall-2010/) · [PDF](https://ocw.mit.edu/courses/22-081j-introduction-to-sustainable-energy-fall-2010/a0eac4cd3f655795dad62021b1a4c769_MIT22_081JF10_lec01b.pdf) | CC BY-NC-SA | 2026-08-19 |
| `MIT11_201F07_slides2.pdf` | 11.201 *Gateway: Planning Action*, Fall 2007 | 城市规划 / 公共政策 | Planning History and Traditions: Overview（15 页） | [课程页](https://ocw.mit.edu/courses/11-201-gateway-planning-action-fall-2007/) · [PDF](https://ocw.mit.edu/courses/11-201-gateway-planning-action-fall-2007/5457ed17ee1ba51263c309c8e20ec2c0_slides2.pdf) | CC BY-NC-SA | 2026-08-19 |
| `MIT15_316SU05_leader_dev.pdf` | 15.316 *Building and Leading Effective Teams*, Summer 2005 | 管理 / 领导力 | How do you develop leadership?（19 页） | [课程页](https://ocw.mit.edu/courses/15-316-building-and-leading-effective-teams-summer-2005/) · [PDF](https://ocw.mit.edu/courses/15-316-building-and-leading-effective-teams-summer-2005/c5a0d8dea9e3b0e77777f4358691f907_leader_dev.pdf) | CC BY-NC-SA | 2026-08-19 |
| `MIT14_129S25_lec02.pdf` | 14.129 *Blockchain and the Design of Financial Systems*, Spring 2025 | 金融 / 计算机 | Lecture 2: Blockchain as a Database（50 页） | [课程页](https://ocw.mit.edu/courses/14-129-blockchain-and-the-design-of-financial-systems-spring-2025/) · [PDF](https://ocw.mit.edu/courses/14-129-blockchain-and-the-design-of-financial-systems-spring-2025/mit14_129_s25_lec02.pdf) | CC BY-NC-SA | 2026-08-19 |

文件已按 MIT 命名惯例重命名（原始 OCW 文件名带哈希前缀，不具可读性）；
原始直链保留在上表中，出处不丢。

## 署名

> MIT OpenCourseWare. License: Creative Commons BY-NC-SA. https://ocw.mit.edu
>
> - 22.081J *Introduction to Sustainable Energy*, Fall 2010.
> - 11.201 *Gateway: Planning Action*, Fall 2007.
> - 15.316 *Building and Leading Effective Teams*, Summer 2005.
> - 14.129 *Blockchain and the Design of Financial Systems*, Spring 2025.

CC BY-NC-SA 的三项义务对本项目的含义：

- **BY（署名）**：即本文件。
- **NC（非商业）**：仅用于本项目的评测与实验。这与 PRD §3.3「v1 不追求盈利与
  商业化验证」一致；若产品后续商业化，测试集需重新审视。
- **SA（相同方式共享）**：约束改编作品，是「使用范围」一节的直接来源。

## 各样本的技术特征

用 `/Subtype /Image` 的尺寸与页面 MediaBox 比对判定，非人工翻阅。

| 文件 | 内嵌字体 | 图像 | 文字绘制算符 | 判定 |
| --- | --- | --- | --- | --- |
| `MIT22_081JF10_lec01b.pdf` | 12 | 20（插图） | 2549 | 文字型（LaTeX beamer） |
| `MIT11_201F07_slides2.pdf` | 1 | 2 | 271 | 文字型，图少、纯文字幻灯片 |
| `MIT15_316SU05_leader_dev.pdf` | **0** | 22，**每页一张全页图**（3134×2271 铺满 805×621 版面） | 589 | **扫描件 + OCR 文本层** |
| `MIT14_129S25_lec02.pdf` | 0 | 228（每页 4–5 张局部插图） | 1431 | 文字型，图表密集 |

### 15.316 是 bad case 样本，不是术语提取样本

它正好命中 PRD §6.2 与 §10 的「扫描件 / 图片型课件」边界，价值在于此，
不应作为术语表提取或讲解质量评测的输入——OCR 文本层已经有明显噪声
（`developleadership` 缺空格、项目符号被识别成 `*` 和 `:`）。

由此得到一条对 F2 的设计要求：**扫描件检测不能用「能否抽出文本」作判据**。
本文件能抽出 589 个文字算符、19 页共约数千字符，任何「有文本即通过」的检查都会
放行它，然后把 OCR 噪声一路带进切片、术语表和讲解。检测判据应当是
**内嵌字体数 ≈ 0 且存在铺满版面的全页图像**——这两个信号在本文件上都成立，
而在其余三份上都不成立。
