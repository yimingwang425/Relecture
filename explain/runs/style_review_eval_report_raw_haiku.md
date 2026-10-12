# 风格审查 agent 评测 · 原始汇总（haiku）

| 指标 | 结果 | 达标线 |
| --- | --- | --- |
| **X** 偏离 → 打回 | 1/64（1.6%） | ≥ 70% |
| **Y** 符合 → 误打回 | 5/64（7.8%） | ≤ 10% |
| **Z** 冲突 → 放行 | 19/20（95.0%） | ≥ 70% |
| 冲突对照（去掉那句要求）→ 打回 | 2/20（10.0%） | — |

## 按轴

| 轴 | X 偏离打回 | Y 符合误打回 | Z 冲突放行 | 对照打回 |
| --- | --- | --- | --- | --- |
| detail_level | 0/16（0.0%） | 2/16（12.5%） | 5/5（100.0%） | 0/5（0.0%） |
| use_examples | 0/16（0.0%） | 1/16（6.2%） | 4/5（80.0%） | 1/5（20.0%） |
| structure | 0/16（0.0%） | 0/16（0.0%） | 5/5（100.0%） | 1/5（20.0%） |
| depth | 1/16（6.2%） | 2/16（12.5%） | 5/5（100.0%） | 0/5（0.0%） |

## 代价

- 条数 168；agent 失败（按放行计）140 条：m01-detail_level-P0~fit TimeoutError: 超过 8 秒未返回；m01-detail_level-P0~dev TimeoutError: 超过 8 秒未返回；m01-detail_level-P3~fit TimeoutError: 超过 8 秒未返回；m01-detail_level-P3~dev TimeoutError: 超过 8 秒未返回；m01-use_examples-more~fit TimeoutError: 超过 8 秒未返回
- 单次成本：均值 $0.0122，中位 $0.0118，= agent 课件题单轮 $0.027 的 +45%（成本未知 137 条）
- 延迟：中位 9.9 s，P90 11.0 s，最大 16.1 s
- 步数分布：{0: 16, 1: 124, 2: 23, 3: 5}；提交被退回合计 6 次
- 工具用法（前 6）：[('measure_explanation', 119), ('measure_explanation,submit_review', 22), ('', 16), ('submit_review', 3), ('measure_explanation,submit_review,submit_review', 3), ('read_recent_turns,measure_explanation,submit_review', 2)]

## 标签效度（预登记的自动检查）

- detail_level 可疑讲解 0 段：无

## 判错的条目

- `m01-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m01-detail_level-P3~dev` 期望 rewrite、得到 None：
- `m01-use_examples-more~dev` 期望 rewrite、得到 None：
- `m01-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m01-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m01-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m01-depth-what~dev` 期望 rewrite、得到 None：
- `m01-depth-derivation~fit` 期望 ok、得到 rewrite：第1段 structure「特征值和特征向量的定义：给定方阵 A、非」改为先说核心判断（例如「有些向量乘以矩阵后，只是被拉伸或压缩，不改变方向」），然
- `m01-depth-derivation~dev` 期望 rewrite、得到 None：
- `m02-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m02-detail_level-P3~dev` 期望 rewrite、得到 None：
- `m02-use_examples-more~dev` 期望 rewrite、得到 None：
- `m02-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m02-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m02-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m02-depth-what~fit` 期望 ok、得到 rewrite：第1段 use_examples「Av = λv 这个式子本身就表示，矩阵」在讲完第1、2段关键概念后，加一个简单例子（如旋转90度、缩放、或其他变换），说
- `m02-depth-what~dev` 期望 rewrite、得到 None：
- `m02-depth-derivation~dev` 期望 rewrite、得到 None：
- `m03-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m03-detail_level-P3~dev` 期望 rewrite、得到 ok：结论在开头、配了具体例子、解释了机制成立的原因，符合所有五项要求。；只举了反射变换一个例子，对于'多举例'的要求略有保守，但对单句讲解来说已足够，总体仍在接受范围。
- `m03-use_examples-more~dev` 期望 rewrite、得到 None：
- `m03-use_examples-fewer~fit` 期望 ok、得到 rewrite：第2段 use_examples「举反射矩阵的例子（第3页）：垂直于镜面线」删除整个第2段，或将其合并入第1段作为进一步说明（但不要称为"举例"）。保留第1
- `m03-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m03-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m03-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m03-depth-what~dev` 期望 rewrite、得到 None：
- `m03-depth-derivation~dev` 期望 rewrite、得到 ok：第1段直接亮出结论，后续段落逐层解释，符合'先给结论'的要求。；镜面反射的具体例子 + 第3段的三种情况对比，举例恰当有力。
- `m04-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m04-detail_level-P3~dev` 期望 rewrite、得到 None：
- `m04-use_examples-more~dev` 期望 rewrite、得到 None：
- `m04-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m04-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m04-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m04-depth-what~dev` 期望 rewrite、得到 None：
- `m04-depth-derivation~dev` 期望 rewrite、得到 None：
- `m05-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m05-detail_level-P3~dev` 期望 rewrite、得到 None：
- `m05-use_examples-more~dev` 期望 rewrite、得到 ok：开篇直接给出对角化的定义和核心公式，符合先给结论的要求；第2段用 P⁻¹P 抵消的推导，以及对照第4页应用场景来解释为什么有用，很好地回答了「为什么」；第3
- `m05-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m05-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m05-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m05-depth-what~dev` 期望 rewrite、得到 ok：结论开头放得好，分解形式清晰；用剪切矩阵和迭代场景作举例，推导步骤交代得也明白
- `m05-depth-derivation~dev` 期望 rewrite、得到 None：
- `m06-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m06-detail_level-P3~fit` 期望 ok、得到 rewrite：第1段 use_examples「旋转没有实数特征值，因为特征向量要求 `」在这段说清特征值的定义后，加一个具体例子。比如：『比如一个向量本来指向东方，旋转
- `m06-detail_level-P3~dev` 期望 rewrite、得到 ok：开头直接给结论，中间逐层解释机制，结尾补充复数例子。结构清晰，详略得当，充分说明了"为什么"。
- `m06-use_examples-more~dev` 期望 rewrite、得到 None：
- `m06-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m06-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m06-structure-buildup_first~dev` 期望 rewrite、得到 ok：第 1–2 段以机制阐述为主，例子较少，但第 3 段的 90° 旋转具体例子弥补了这一点，整体达到了；总字数 388 字在参考范围内，各段长度适中，信息量符合'详细'的要求
- `m06-depth-what~dev` 期望 rewrite、得到 None：
- `m06-depth-derivation~dev` 期望 rewrite、得到 None：
- `m07-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m07-detail_level-P3~dev` 期望 rewrite、得到 None：
- `m07-use_examples-more~dev` 期望 rewrite、得到 ok：讲解按学生的偏好展开：第1段直接给出核心结论，第2段说做法，第3段深入解释机制原理，第4段说明算法设
- `m07-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m07-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m07-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m07-depth-what~dev` 期望 rewrite、得到 ok：讲解整体符合详细、多举例、先结论、讲推导的要求。第1段结论清晰，第2段推导链条明确（每步乘以特征值、
- `m07-depth-derivation~dev` 期望 rewrite、得到 None：
- `m08-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m08-detail_level-P3~fit` 期望 ok、得到 rewrite：第1段 use_examples「PCA 用协方差矩阵，找它伸缩最大的方向」在说明 PCA 和特征向量的关系后，加一个简单的具体例子，比如用二维散点数据说明
- `m08-detail_level-P3~dev` 期望 rewrite、得到 None：
- `m08-use_examples-more~dev` 期望 rewrite、得到 None：
- `m08-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m08-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m08-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m08-depth-what~dev` 期望 rewrite、得到 None：
- `c01-detail_level~control` 期望 rewrite、得到 None：
- `c01-use_examples~control` 期望 rewrite、得到 None：
- `c01-structure~control` 期望 rewrite、得到 None：
- `c01-depth~control` 期望 rewrite、得到 None：
- `c02-detail_level~control` 期望 rewrite、得到 None：
- `c02-use_examples~control` 期望 rewrite、得到 None：
- `c02-structure~control` 期望 rewrite、得到 None：
- `c02-depth~control` 期望 rewrite、得到 None：
- `c03-detail_level~control` 期望 rewrite、得到 None：
- `c03-depth~control` 期望 rewrite、得到 None：
- `c04-detail_level~control` 期望 rewrite、得到 None：
- `c04-use_examples~conflict` 期望 ok、得到 rewrite：第4段 use_examples「课件里只给了剪切这一个具体例子，没有列举」删除这段说明，改为补充给出 2–3 个具体的不可对角化矩阵的例子（例如含有 Jo
- `c04-use_examples~control` 期望 rewrite、得到 None：
- `c04-structure~control` 期望 rewrite、得到 None：
- `c04-depth~control` 期望 rewrite、得到 None：
- `c05-detail_level~control` 期望 rewrite、得到 None：
- `c05-use_examples~control` 期望 rewrite、得到 None：
- `c05-structure~control` 期望 rewrite、得到 None：
- `c05-depth~control` 期望 rewrite、得到 None：
