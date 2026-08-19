# 测试集出处

本目录存放评测与实验用的课件样本。**只收公开授权材料**，同学课件一律不入库
（PRD §12.1：课件仅存于用户个人空间，不建公共库）。

新增文件必须同时在此表登记出处、许可与下载日期。无出处记录的文件视为不合规，
应从版本库移除。

| 文件 | 课程 | 内容 | 来源 | 许可 | 下载日期 |
| --- | --- | --- | --- | --- | --- |
| `MIT22_081JF10_lec01b.pdf` | MIT 22.081J / 2.650J / 10.291J / 1.818J / 2.65J / 10.391J / 11.371J / 22.811J / ESD.166J — Introduction to Sustainable Energy, Fall 2010 | Lecture 1b: Overview of energy use and related issues（39 页） | [ocw.mit.edu](https://ocw.mit.edu/courses/22-081j-introduction-to-sustainable-energy-fall-2010/) · [PDF 直链](https://ocw.mit.edu/courses/22-081j-introduction-to-sustainable-energy-fall-2010/a0eac4cd3f655795dad62021b1a4c769_MIT22_081JF10_lec01b.pdf) | CC BY-NC-SA（以 OCW 课程页标注为准） | 2026-08-19 |

## 署名

> MIT OpenCourseWare, 22.081J *Introduction to Sustainable Energy*, Fall 2010.
> License: Creative Commons BY-NC-SA. https://ocw.mit.edu

CC BY-NC-SA 的三项义务对本项目的含义：

- **BY（署名）**：即本文件。任何对外展示（面试材料、演示、博客）引用到该课件
  内容时同样需要署名。
- **NC（非商业）**：仅用于本项目的评测与实验。这与 PRD §3.3「v1 不追求盈利与
  商业化验证」一致；若产品后续商业化，测试集需重新审视。
- **SA（相同方式共享）**：约束的是该材料的**改编作品**。模型基于它生成的讲解
  文本若要公开分发，需评估是否构成改编。评测集内部使用不触发此项。

## 该样本的技术特征

已核验为文字型 PDF（12 个内嵌字体、2549 个文字绘制算符），可直接抽取文本，
符合 PRD §6.2 的可处理前提；同时含 20 个图像对象（图表插图），是 F2 切片模块
需要处理的真实形态——纯文字 PDF 反而不具代表性。

## 与 A 类规则计算脚本的关系

A 类脚本的输入是**讲解文本**，不是课件本身，因此不直接消费本目录的 PDF。
本目录服务于：

1. F2 课件上传与处理的开发与测试
2. 术语表提取（PRD §7.1：审查侧的分母来源）——`term_annotation` 维度的术语表
   就从这里产出
3. PRD §8.2 漂移实验的固定课件输入
