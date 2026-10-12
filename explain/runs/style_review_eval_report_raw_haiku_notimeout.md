# 风格审查 agent 评测 · 原始汇总（haiku_notimeout）

| 指标 | 结果 | 达标线 |
| --- | --- | --- |
| **X** 偏离 → 打回 | 29/64（45.3%） | ≥ 70% |
| **Y** 符合 → 误打回 | 27/64（42.2%） | ≤ 10% |
| **Z** 冲突 → 放行 | 11/20（55.0%） | ≥ 70% |
| 冲突对照（去掉那句要求）→ 打回 | 12/20（60.0%） | — |

## 按轴

| 轴 | X 偏离打回 | Y 符合误打回 | Z 冲突放行 | 对照打回 |
| --- | --- | --- | --- | --- |
| detail_level | 7/16（43.8%） | 10/16（62.5%） | 5/5（100.0%） | 2/5（40.0%） |
| use_examples | 9/16（56.2%） | 3/16（18.8%） | 1/5（20.0%） | 2/5（40.0%） |
| structure | 6/16（37.5%） | 7/16（43.8%） | 3/5（60.0%） | 5/5（100.0%） |
| depth | 7/16（43.8%） | 7/16（43.8%） | 2/5（40.0%） | 3/5（60.0%） |

## 代价

- 条数 168；agent 失败（按放行计）38 条：m01-detail_level-P0~dev 3 步之内没有交出通过检查的结论（退回 2 次）；m03-structure-conclusion_first~dev 3 步之内没有交出通过检查的结论（退回 2 次）；m04-detail_level-P0~fit 3 步之内没有交出通过检查的结论（退回 2 次）；m04-detail_level-P0~dev 3 步之内没有交出通过检查的结论（退回 2 次）；m04-use_examples-more~dev 3 步之内没有交出通过检查的结论（退回 2 次）
- 单次成本：均值 $0.0166，中位 $0.0136，= agent 课件题单轮 $0.027 的 +61%（成本未知 0 条）
- 延迟：中位 12.9 s，P90 20.7 s，最大 27.1 s
- 步数分布：{1: 4, 2: 96, 3: 68}；提交被退回合计 109 次
- 工具用法（前 6）：[('measure_explanation,submit_review', 94), ('measure_explanation,submit_review,submit_review', 66), ('submit_review', 4), ('submit_review,submit_review,submit_review', 2), ('read_recent_turns,submit_review', 1), ('submit_review,submit_review', 1)]

## 标签效度（预登记的自动检查）

- detail_level 可疑讲解 0 段：无

## 判错的条目

- `m01-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m01-detail_level-P3~fit` 期望 ok、得到 rewrite：第2段 detail_level「多数向量经 A 变换后会偏离原来的直线，」删减这段的详细说明（关于 λ 值的分类讨论），改为简明一句，空出篇幅为其他要点配；第1段 use_examples「特征向量是一个非零向量 v，被矩阵 A 」在定义后立即举一个简单例子（如旋转矩阵的某特征向量），使每个要点都有对应例子。
- `m01-detail_level-P3~dev` 期望 rewrite、得到 ok：各项偏好贯彻得好：开头就亮出结论，逐段深化理解，穿插几何直觉和具体例子，说明了特征向量为什么特殊。
- `m01-use_examples-fewer~fit` 期望 ok、得到 rewrite：第1段 structure「特征值和特征向量的定义：设 A 是方阵，」改为先用一句说出要点（如「特征向量是矩阵作用下不改变方向、只做缩放的特殊方向」）；第5段 use_examples「反射变换中，镜面线上的向量不动，特征值为」删除具体的几何变换例子，在第2段的直观描述中已说明原理，不需补充特定场景
- `m01-use_examples-fewer~dev` 期望 rewrite、得到 ok：整篇420字略超参考值360字，但超幅仅17%，且每段长度分布合理；整体确实是详细风格，可放行。；第1段直接给出定义和结论，后续段落依次展开直观理解、边界情况、具体例子、设计意义，结构层次清晰。
- `m01-structure-conclusion_first~dev` 期望 rewrite、得到 ok：整篇 414 字略超参考 360 字，但幅度不大，各段长度合理。；结构清晰（先问题后结论），例子恰当（镜面反射、缩放矩阵），深度充分（解释了为什么），整体符合风格选择
- `m01-structure-buildup_first~dev` 期望 rewrite、得到 ok：五项要求全部符合：开头先给结论（第1段），详细程度适中（段长30–120字），配有三个生动例子（第6；整篇472字略超参考的360字，但内容紧凑充实，每段控制在范围内，属可接受的灵活范围。
- `m01-depth-derivation~fit` 期望 ok、得到 rewrite：第1段 depth「给定方阵 A、非零向量 v 和数 λ，如」在给出定义前，先展开几何观察：矩阵作用于向量时，大多数向量方向会改变，但某些特殊
- `m01-depth-derivation~dev` 期望 rewrite、得到 ok：讲法整体符合学生选择。定义在前，例子充分，长度和详略适中，全中文讲解。；第3–4段列表分解λ符号意义处略显碎化，但不影响先给结论、后做解释的主框架。
- `m02-detail_level-P3~fit` 期望 ok、得到 rewrite：第1段 detail_level「特征向量方向不变，是因为它定义上满足 A」压缩到"特征向量方向不变：Av = λv 意味着结果仍在原直线上，只放大/缩小 ；第2段 detail_level「大多数向量会被矩阵转到一条新的线上，但特」这段对比解释有效但篇幅较长，可改为简洁过渡句："普通向量会被转到新方向，特征向量；第3段 detail_level「比如镜面反射：沿镜面的向量不变（λ=1）」保留例子但精简："例如镜面反射：沿镜面的向量不变，垂直镜面被翻转到反方向，但仍在
- `m02-detail_level-P3~dev` 期望 rewrite、得到 ok：结构清晰，先给结论后解释；举了恰当的镜面反射例子；深度到位，既说清了定义关系，又解释了特殊性。字数和
- `m02-depth-what~fit` 期望 ok、得到 rewrite：第1段 structure「特征向量方向不变是特征值概念的定义结果」改为直接的结论：第一句改成'特征向量在矩阵变换后方向不变，就是因为它们满足 Av；第2段 use_examples「一般的向量被 A 作用后，会落到一条新的」讲完这段对比后，在第2段或第3段初补充一个旋转或其他变换的具体例子；第4段 depth「负的特征值让向量指向反方向，但课件里仍把」删除第4段关于设计理由的讨论，改为直接指出：特征向量的定义就是满足 Av = λ
- `m02-depth-what~dev` 期望 rewrite、得到 ok：4段370字，段长30-121字，详细程度和篇幅都符合要求。；第3段给了镜面反射的具体例子，展示了λ=1和λ=-1两种情况，有效支撑了前面的抽象解释。
- `m02-depth-derivation~fit` 期望 ok、得到 rewrite：第1段 use_examples「乘一个数只会改变向量的长度（以及正负方向」在解释'乘数只改变长度和正负'后，加一个简单的数字例子（比如二维向量的具体乘法）
- `m02-depth-derivation~dev` 期望 rewrite、得到 ok：整篇 387 字，比参考上限多一点，但在接受范围内；段落长度均衡且详细；开头第一句直接给出「定义决定」的结论，之后逐段论证，符合「先结论后解释」的要求
- `m03-detail_level-P0~fit` 期望 ok、得到 rewrite：第1段 use_examples「特征值为负，意味着矩阵把这个向量沿着原来」在解释「翻转到反方向」后，立即给一个简单具体例子，比如在数轴上的缩放变换或旋转 ；第3段 use_examples「反复乘以矩阵会让向量在这条线上来回摆动、」补充一个具体例子，比如某个特征值为 -2 的方向，第一次乘后向量翻转并放大 2 
- `m03-detail_level-P3~dev` 期望 rewrite、得到 ok：符合「先给结论→再讲机制」的结构，例子恰当，详细程度也在预期范围
- `m03-use_examples-fewer~fit` 期望 ok、得到 rewrite：第2段 use_examples「举反射矩阵的例子（第3页）：垂直于镜面线」删除第2段整段，直接在第1段补充说明"比例因子为负号"的含义即可。第1和第3段已
- `m03-use_examples-fewer~dev` 期望 rewrite、得到 ok：开头直接交代核心判断，后文逐层展开原因和应用——很好地践行了'先给结论'的要求；反射矩阵和迭代系统两个例子互补，一个说静态含义，一个说动态效果，每个要点都有支撑
- `m03-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m03-structure-buildup_first~dev` 期望 rewrite、得到 ok：开头结论鲜明，例子（反射矩阵、摆动）具体到位，解释了机制（方向翻转、符号交替缩放），字数和段落长度恰
- `m03-depth-what~dev` 期望 rewrite、得到 ok：四项核心要求都符合：开头结论(翻转+缩放)、配例子(镜像反射)、段落适中、推导逻辑清晰
- `m03-depth-derivation~dev` 期望 rewrite、得到 ok：结论在第1段第一句就给出，符合"先给结论"的要求；第2段提供了镜面反射的具体例子，第3段用对比展示不同特征值的效果，举例充分
- `m04-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m04-detail_level-P3~fit` 期望 ok、得到 rewrite：第1段 use_examples「特征值是使 A − λI 不可逆的数 λ」第1段的定义需要配一个简单例子来说明什么是特征值、什么是特征向量，比如一个具体的；第3段 detail_level「例如2×2矩阵，这个方程是二次的，可以用」这段字数较多且包含两个不同的信息点（求根方法和检验技巧）；建议只保留一个要点，或
- `m04-use_examples-more~dev` 期望 rewrite、得到 None：
- `m04-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m04-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m04-structure-buildup_first~fit` 期望 ok、得到 rewrite：第1段 structure「求特征值的核心思路在第8页：找到那些使 」先从「要找哪些特征值」或「特征值的定义」开始，说完问题和机制后，最后在第3、4段；第2段 use_examples「具体做法是令 `det(A − λI) 」这个步骤配个具体2×2或3×3矩阵的例子，展示怎么从 `A − λI` 到特征多
- `m04-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m04-depth-what~fit` 期望 ok、得到 rewrite：第3段 depth「实际数值计算中没人会真的去解特征多项式，」删除这段关于「为什么不用特征多项式」的设计理由，只保留「QR算法和幂法是计算机常
- `m04-depth-what~dev` 期望 rewrite、得到 None：
- `m05-detail_level-P0~fit` 期望 ok、得到 rewrite：第1段 structure「对角化（第9页）：如果一个 n×n 矩阵」第1段应改为先说结论，如：「对角化是把矩阵改写成容易计算的形式，特别是在反复求幂；第4段 use_examples「用处主要在算矩阵的幂。直接算 A 的 1」在讲 A¹⁰⁰ 的计算优势时，给一个 2×2 具体矩阵的例子，写出特征值后，对比
- `m05-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m05-detail_level-P3~fit` 期望 ok、得到 rewrite：第1段 detail_level「对角化：若 n×n 矩阵有 n 个线性无」精简到15-25字，建议改为类似「对角化就是把矩阵写成 A = PDP⁻¹ 的形；第2段 use_examples「用处：计算 A 的幂时，只需对 D 的对」补充具体例子，如「比如算 A¹⁰⁰，对角化后只需对角元素做100次单独的乘法，比；第3段 detail_level「但不是所有矩阵都能对角化——比如剪切矩阵」精简到30字左右，核心保留「剪切矩阵无法对角化，需用Jordan标准形」即可
- `m05-use_examples-more~fit` 期望 ok、得到 rewrite：第1段 use_examples「如果 n×n 矩阵有 n 个线性无关的特」在定义后补充一个具体例子，比如 2×2 的旋转矩阵或缩放矩阵，给出它的特征向量和；第2段 use_examples「A² = P D P⁻¹ P D P⁻¹」用第1段的那个具体矩阵，实际数值计算一遍 A²，对比直接算矩阵乘法和用对角化后算；第3段 use_examples「像剪切（shear）这种矩阵，有重复的特」给出剪切矩阵的具体例子（如 [[1,1],[0,1]]），算出它的特征值和特征向
- `m05-use_examples-more~dev` 期望 rewrite、得到 ok：三段字数分别 69、155、79 字，整篇 303 字，符合详细程度和长度参考。；第一段开头直接给出对角化的定义和关键公式，后续段落逐步展开用途和局限，完整体现了先结论后解释的结构。
- `m05-structure-conclusion_first~fit` 期望 ok、得到 rewrite：第1段 use_examples「对角化是把矩阵 A 写成 A = PDP」在定义之后或第2段开头补充一个具体的小矩阵例子（如2×2的简单矩阵），展示怎样实；第2段 use_examples「A¹⁰⁰ = P D¹⁰⁰ P⁻¹，而对」补充一个具体数值例子，比如用上面那个2×2矩阵，算一次对角化后的平方或立方结果，
- `m05-structure-buildup_first~dev` 期望 rewrite、得到 ok：第 3 段的迭代系统例子很有效。第 4 段关于对角化局限性的陈述很直接，不加例子反而保持了简洁。；开头第 1 段直接给出定义和公式，后续逐层展开用途、计算优势、边界情况，逻辑清晰。
- `m05-depth-what~dev` 期望 rewrite、得到 ok：结构清晰，结论优先，推导步骤明确，举例充分（剪切矩阵、迭代系统两个例子恰当），字数和详细度符合要求。
- `m06-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m06-detail_level-P3~fit` 期望 ok、得到 rewrite：第2段 use_examples「而旋转（非半圈的倍数）会把所有方向都转到」加入具体例子说明，比如'比如90度旋转把水平方向转到竖直方向，没有任何方向被保留；第2段 detail_level「既然找不到满足 Av=λv 的实向量，实」这句话的逻辑已包含在前面"没有一条直线能保持不动"中，可以删除来减少重复，从而压
- `m06-structure-buildup_first~fit` 期望 ok、得到 rewrite：第1段 use_examples「旋转把平面上每个方向都转到另一个方向，除」在这个陈述后添加具体例子。比如补充：「比如 90° 旋转，竖直向上的向量被转到水
- `m06-structure-buildup_first~dev` 期望 rewrite、得到 None：
- `m06-depth-what~fit` 期望 ok、得到 rewrite：第1段 use_examples「旋转会把平面上几乎所有方向都转到另一条线」在说完特征向量的要求后，立即加一个简单例子（如"比如把一个指向右的向量变成指向上
- `m06-depth-what~dev` 期望 rewrite、得到 ok：开头直接给出结论，后续逐层解释和推导，符合'先给结论'的要求。；第2段完整推导了'旋转不保持任何直线→无满足Av=λv的方向→无实数特征值'的逻辑链。
- `m06-depth-derivation~fit` 期望 ok、得到 rewrite：第1段 structure「旋转没有实数特征值，是因为特征向量要求」把「旋转没有实数特征值」这句改成明确的开头结论，不要从「特征向量的含义」开始讲。；第3段 use_examples「假设存在实特征向量 v 和实数 λ，使 」在讲这个推理时插入一个具体例子：例如90°旋转矩阵作用在某个具体向量上的效果，或
- `m07-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m07-detail_level-P3~fit` 期望 ok、得到 rewrite：第2段 detail_level「做法是：取一个初始向量，不断用矩阵 A 」大幅精简：简化为「初始向量 → 反复乘以 A 和归一化」，删除对"防止数值爆炸"；第2段 use_examples「做法是：取一个初始向量，不断用矩阵 A 」在第 2 段做法后立即举一个简单例子：比如 2×2 矩阵的具体迭代过程，或一个数
- `m07-use_examples-more~dev` 期望 rewrite、得到 ok：完全符合学生偏好。开头第一句就给出核心结论，后续逐层深入；用矩阵分解论证原理，解释了机制'为什么'；
- `m07-use_examples-fewer~dev` 期望 rewrite、得到 None：
- `m07-structure-conclusion_first~fit` 期望 ok、得到 rewrite：第1段 use_examples「幂法(power method)是求矩阵」在解释完这个定义后，加一个简短的具体例子说明'不断用矩阵乘向量、每次缩放'这个流；第4段 use_examples「课件第10页提到，幂法只是最简单的迭代思」删除或大幅精简第4段关于QR算法的补充，这不是'幂法是怎么回事'这个问题的核心。；第2段 detail_level「任何起始向量都可以看成各个特征方向的叠加」保留这个逻辑链条，但删除第10页的英文引文部分，让整段更精炼，控制在100-12
- `m07-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m07-structure-buildup_first~fit` 期望 ok、得到 rewrite：第1段 use_examples「反复用矩阵去乘一个向量，每乘一次，向量在」举一个具体的 2×2 矩阵例子，用数字演示向量在特征方向上如何被放大或缩小；第2段 use_examples「经过多次相乘后，对应最大特征值（绝对值最」在这里或第3段给出一个数值序列的例子，让读者看到向量分量的具体增长过程
- `m07-depth-what~dev` 期望 rewrite、得到 ok：结论优先摆在第1段，后面依次通过原理推导、直观类比和背景补充来支撑，符合'先给结论'+'讲到推导'的
- `m08-detail_level-P0~fit` 期望 ok、得到 rewrite：第1段 use_examples「特征向量指出方向，对应的特征值告诉你数据」在这句后面补充一个简单的例子或类比，比如；第2段 use_examples「特征值都是实数，不同特征值对应的特征向量」补充具体例子来直观说明垂直性和拉伸的含义，比如在二维平面上给出椭圆的例子
- `m08-detail_level-P0~dev` 期望 rewrite、得到 None：
- `m08-detail_level-P3~fit` 期望 ok、得到 rewrite：第1段 use_examples「PCA 用协方差矩阵，找它伸缩最大的方向」在第1段或第2段加入一个具体例子或类比：比如说明在二维数据中，协方差矩阵的最大特
- `m08-structure-conclusion_first~fit` 期望 ok、得到 rewrite：第1段 use_examples「PCA 要找数据」第1段之后或段内补加一个具体数据集的例子（如二维散点图），说明；第2段 use_examples「特征值都是实数，不同特征值对应的特征向量」补加一个具体的 2×2 对称矩阵例子，列出它的特征向量和特征值，显示它们如何垂直；第3段 use_examples「特征值越大，说明数据在这个方向上方差越大」用数值例子说明：协方差矩阵的哪个特征值对应哪个方向，说明该方向数据的实际伸展程度
- `m08-structure-conclusion_first~dev` 期望 rewrite、得到 None：
- `m08-structure-buildup_first~fit` 期望 ok、得到 rewrite：第2段 use_examples「整个矩阵的作用相当于沿着这些互相垂直的方」在这个几何直观之后，加一个具体例子，如"比如一个2×2的协方差矩阵，特征值为3和
- `m08-depth-what~fit` 期望 ok、得到 rewrite：第1段 structure「PCA（主成分分析）处理的是数据的协方差」把这个结论调到开头作为第一句的主干，不要以「协方差矩阵」为主语开场；可以改为：「；第2段 use_examples「把数据投影到这些相互正交的特征向量方向上」在讲「投影到特征向量方向」后，举一个简单例子：比如二维平面上的散点图，PCA 会
- `c01-use_examples~conflict` 期望 ok、得到 rewrite：第3段 use_examples「课件里没有给出具体数值的例子（比如代入真」学生明确要求'多举几个具体的例子'，第3段应该直接给出2-3个具体的数值矩阵例子
- `c01-use_examples~control` 期望 rewrite、得到 ok：整体符合要求。结论优先，推导链条清晰，解释了关键步骤（齐次方程组何时有非零解），没有冗余例子。
- `c01-depth~control` 期望 rewrite、得到 None：
- `c02-use_examples~conflict` 期望 ok、得到 rewrite：第7段 use_examples「课件没有给出具体数值矩阵（比如某个3×3」删除这一段，改为补充 2–3 个具体的对称矩阵例子，写出数值矩阵、计算其特征值和
- `c02-use_examples~control` 期望 rewrite、得到 None：
- `c02-depth~conflict` 期望 ok、得到 rewrite：第2段 depth「不过课件在第5页只给出了这个**结论**」删除第2-5段的迂回说辞。直接在第1段结论之后展开完整推导，而不是问学生要不要继；第4段 use_examples「设 Av₁=λ₁v₁，Av₂=λ₂v₂，」每个推导步骤配一个具体的2×2或3×3对称矩阵数字例子，计算出其特征值和特征向量
- `c02-depth~control` 期望 rewrite、得到 None：
- `c03-detail_level~control` 期望 rewrite、得到 None：
- `c03-use_examples~conflict` 期望 ok、得到 rewrite：第4段 use_examples「反射矩阵（第3页）：特征值是1和−1。迹」删除或大幅简化后面的缩放和旋转矩阵例子，只保留1-2个核心例子，使第4段字数与少
- `c04-detail_level~control` 期望 rewrite、得到 None：
- `c04-use_examples~conflict` 期望 ok、得到 rewrite：第4段 use_examples「课件里只给了剪切这一个具体例子，没有列举」删除第4段，在第3段后补充2-3个具体的不可对角化矩阵例子（如具体的2×2 Jo
- `c04-structure~conflict` 期望 ok、得到 rewrite：第1段 structure「先看剪切变换（shear）的效果：它把一」改成推导式：从剪切矩阵的具体例子开始，让学生自己探索特征值特征向量，最后推导出对；第2段 detail_level「要对角化，第9页说得很清楚：需要找到 n」这段提前透露了答案的关键。推导式讲法应该先让学生跟着算特征值、特征向量，在碰到矛
- `c04-depth~conflict` 期望 ok、得到 rewrite：第4段 depth「第二步，按第8页的方法求特征值：解 de」在展开逐步推导前，先确认这一次是否允许突破「只讲是什么」的原则。应在讲解开头加一
- `c05-detail_level~control` 期望 rewrite、得到 None：
- `c05-use_examples~control` 期望 rewrite、得到 None：
- `c05-structure~conflict` 期望 ok、得到 rewrite：第2段 structure「每作用一次 A，每个分量只按自己的特征值」第2段不应该直接给出这个关键结论。应该先让学生从第1段的向量分解出发，逐步推导出
- `c05-depth~conflict` 期望 ok、得到 rewrite：第1段 structure「任取一个起始向量 v，把它按特征向量方向」开头第一段改为：最大特征值决定了长期行为。反复作用矩阵时，最大特征值对应的项被λ；第7段 use_examples「Aᵏv = λ₁ᵏ [c₁v₁ + c₂」在此后补加一个具体例子，比如：假设λ₁=2，λ₂=0.5，那么当k=10时，(0
