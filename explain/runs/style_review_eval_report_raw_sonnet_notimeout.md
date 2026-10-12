# 风格审查 agent 评测 · 原始汇总（sonnet_notimeout）

| 指标 | 结果 | 达标线 |
| --- | --- | --- |
| **X** 偏离 → 打回 | 21/32（65.6%） | ≥ 70% |
| **Y** 符合 → 误打回 | 8/32（25.0%） | ≤ 10% |
| **Z** 冲突 → 放行 | 16/20（80.0%） | ≥ 70% |
| 冲突对照（去掉那句要求）→ 打回 | 17/20（85.0%） | — |

## 按轴

| 轴 | X 偏离打回 | Y 符合误打回 | Z 冲突放行 | 对照打回 |
| --- | --- | --- | --- | --- |
| detail_level | 5/8（62.5%） | 3/8（37.5%） | 4/5（80.0%） | 4/5（80.0%） |
| use_examples | 7/8（87.5%） | 2/8（25.0%） | 5/5（100.0%） | 5/5（100.0%） |
| structure | 6/8（75.0%） | 1/8（12.5%） | 4/5（80.0%） | 5/5（100.0%） |
| depth | 3/8（37.5%） | 2/8（25.0%） | 3/5（60.0%） | 3/5（60.0%） |

## 代价

- 条数 104；agent 失败（按放行计）3 条：m01-structure-buildup_first~dev 3 步之内没有交出通过检查的结论（退回 2 次）；m03-depth-derivation~fit 3 步之内没有交出通过检查的结论（退回 2 次）；c03-detail_level~control 3 步之内没有交出通过检查的结论（退回 2 次）
- 单次成本：均值 $0.0255，中位 $0.0236，= agent 课件题单轮 $0.027 的 +94%（成本未知 0 条）
- 延迟：中位 6.8 s，P90 14.0 s，最大 22.4 s
- 步数分布：{1: 49, 2: 35, 3: 20}；提交被退回合计 32 次
- 工具用法（前 6）：[('submit_review', 49), ('measure_explanation,submit_review', 32), ('measure_explanation,submit_review,submit_review', 14), ('submit_review,submit_review,submit_review', 6), ('submit_review,submit_review', 3)]

## 标签效度（预登记的自动检查）

- detail_level 可疑讲解 0 段：无

## 判错的条目

- `m01-detail_level-P0~fit` 期望 ok、得到 rewrite：第1段 structure「特征向量是一个非零向量」开头第一句应先给出结论性判断，再展开定义和解释，而不是直接给公式定义。；第1段 depth「伸缩的倍数就是 `λ`」应补充解释为什么要定义这种不变方向、这样设计的意义是什么，而不只是给出定义和举例
- `m01-detail_level-P3~fit` 期望 ok、得到 rewrite：第2段 detail_level「但特征向量保持在自己那条线上，只是长度变」精简到一两句话，去掉过多的分支枚举，整篇压缩到120字以内；第1段 depth「被矩阵 A 作用后方向不变」补充一句为什么矩阵会存在这样的特殊方向，而不仅是给出定义
- `m01-use_examples-fewer~fit` 期望 ok、得到 rewrite：第5段 use_examples「反射变换中，镜面线上的向量不动，特征值为」删去这个举例段落，把边界情况（负特征值、零特征值）的说明直接并入要点讲解，不额外；第1段 structure「那么 `v` 就是 `A` 的特征向量」开头第一句应先给出最核心的结论性判断（特征向量是矩阵作用下方向不变、只被缩放的特
- `m01-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m01-depth-derivation~fit` 期望 ok、得到 rewrite：第1段 structure「那么 `v` 就是 `A` 的特征向量，」开头第一段应先给出最重要的结论性判断（如“特征向量是矩阵作用下方向不变、只被拉伸；第2段 depth「只是把它沿着原来的线拉伸或压缩，拉伸的倍」应补充从 Av=λv 出发一步步推出关键结论的过程（如推导特征方程 det(A-
- `m01-depth-derivation~dev` 期望 rewrite、得到 ok：第1段直接给出定义作为核心结论，符合先结论后解释的要求；第5、6段给了镜面对称、缩放、旋转反例等具体例子，举例充分
- `m02-detail_level-P3~dev` 期望 rewrite、得到 ok：开头第一段即给出核心结论 Av=λv，符合先结论后解释的要求。；第3段镜面反射的例子贴切，帮助理解方向不变的含义。
- `m02-structure-buildup_first~fit` 期望 ok、得到 rewrite：第1段 structure「特征向量方向不变，是因为它定义上就要求了」学生要求先铺垫（问题→机制）再在最后给出结论判断，但第1段开头就直接给出了结论性
- `m02-depth-derivation~dev` 期望 rewrite、得到 ok：第1段开头即给出结论（定义决定方向不变），符合先结论后解释的要求。；学生本轮明确问为什么，讲解做了针对性解释，这是对学生当前提问的合理响应，优先于默认的'只讲是什么'偏
- `m03-detail_level-P0~dev` 期望 rewrite、得到 ok：每段简短，整体精简，符合要求；第2段给出反射变换的具体例子，第3段也有迭代的形象说明
- `m03-use_examples-fewer~fit` 期望 ok、得到 rewrite：第2段 use_examples「举反射矩阵的例子（第3页）：垂直于镜面线」删去反射矩阵这个额外举例，直接用文字说明负特征值表示翻转加缩放的含义即可，不必补
- `m03-use_examples-fewer~dev` 期望 rewrite、得到 ok：开头即给出结论性判断（负特征值=同向线上翻转），符合先结论后解释。；第2段用反射矩阵例子辅助理解。
- `m03-depth-what~dev` 期望 rewrite、得到 ok：篇幅221字略短于参考下限，但内容完整，可接受；第2段给出镜面反射的具体例子，满足举例要求
- `m03-depth-derivation~dev` 期望 rewrite、得到 ok：第1段直接给出结论（负特征值=方向翻转但仍在同一直线上），符合先结论后解释。；第2段给了反射矩阵的具体例子，第3段用对比例子（正/负/零特征值）帮助理解，举例充分。
- `m04-detail_level-P3~fit` 期望 ok、得到 rewrite：第1段 structure「特征值是使 A − λI 不可逆的数 λ」开头第一句应直接给出求法的结论，例如先说「解 det(A−λI)=0 的根就是特
- `m04-detail_level-P3~dev` 期望 rewrite、得到 ok：开头以定义+条件切入，隐含给出核心判断，总体可接受；仅第3段有具体例子，略少但不算严重偏离
- `m04-structure-buildup_first~dev` 期望 rewrite、得到 ok：第1段直接给出核心判断，后文逐步展开，符合先结论后解释。；第3段给了具体数字例子，第4段用类比说明幂法，举例到位。
- `m04-depth-what~fit` 期望 ok、得到 rewrite：第3段 depth「e」placeholder
- `m04-depth-what~dev` 期望 rewrite、得到 ok：第1段开头即给出求特征值的核心方法/结论，符合先结论后解释的要求。；给出了det(A-λI)=0的由来和校验公式，但没有展开严格推导过程，深度略浅于理想但可接受。
- `c01-structure~conflict` 期望 ok、得到 rewrite：第4段 language「Eigenvalues are the 」这是整句英文直接引用，不是术语括注原文，应改写为中文讲解，必要的术语可用「特征值
- `c01-depth~control` 期望 rewrite、得到 ok：第1段先给出结论性判断（det(A-λI)=0的来源），符合先结论后解释的结构。；学生本句明确问『怎么来的』，要求推导，优先于预存的『只讲是什么』偏好，此处详细推导合理。
- `c02-depth~conflict` 期望 ok、得到 rewrite：第4段 depth「设 Av = λv，两边取共轭转置并利用」学生这一句明确要求把推导过程一步步写出来，应直接完整展开每一步推导（如实数性：取
- `c03-detail_level~control` 期望 rewrite、得到 None：
- `c04-depth~control` 期望 rewrite、得到 ok：学生本轮明确问
- `c05-detail_level~conflict` 期望 ok、得到 rewrite：第2段 use_examples「那么λ₁^k相对其他λᵢ^k的比值会随k」配一个具体数值例子，比如取λ₁=2、λ₂=1，算几步后2^k比1^k大多少，帮助；第4段 use_examples「不断用矩阵乘向量再归一化，最终收敛到最大」举一个简单矩阵反复作用并归一化、逐步逼近主方向的小例子或生活类比，而不是只做抽象
- `c05-depth~conflict` 期望 ok、得到 rewrite：第1段 structure「任取一个起始向量 v，把它按特征向量方向」开头先给出结论句，例如先说明「反复作用矩阵后，模最大的特征值对应项会指数级压制其；第10段 use_examples「也就是说，除了最大特征值对应的项，其余项」在讲解过程中加入至少一个具体数值例子或类比（比如用一个2x2矩阵和具体特征值数字
