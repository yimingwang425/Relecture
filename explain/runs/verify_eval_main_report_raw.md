## main · haiku · v1

| 类型 | n | 抓到 | 检出率 | 判 supported | 归为 supplement | 没提取 | 核对失败 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| number | 15 | 6 | 40% | 5 | 1 | 2 | 1 |
| concept | 21 | 3 | 14% | 14 | 1 | 1 | 2 |
| page | 27 | 23 | 85% | 0 | 0 | 0 | 4 |

未改样例 30 段：逐段误报 1/30；逐条误报 1/76 条 fact（1.3%）

单次核对：均值 $0.0210（n = 93）· 延迟均值 16.7 s · 状态 {'written': 84, 'failed': 9}
占单轮讲解成本：固定流水线 $0.0151 的 139% · agent 课件题 $0.027 的 78%

误报待人工复核：
- depth_what_why.b:clean｜not_found｜λ=0对应的特征向量能揭示A的零空间（null space）：这些方向就是A的

## main · sonnet · v1

| 类型 | n | 抓到 | 检出率 | 判 supported | 归为 supplement | 没提取 | 核对失败 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| number | 9 | 9 | 100% | 0 | 0 | 0 | 0 |
| concept | 11 | 9 | 82% | 2 | 0 | 0 | 0 |
| page | 13 | 13 | 100% | 0 | 0 | 0 | 0 |

未改样例 15 段：逐段误报 2/15；逐条误报 4/45 条 fact（8.9%）

单次核对：均值 $0.0333（n = 48）· 延迟均值 9.0 s · 状态 {'written': 48}
占单轮讲解成本：固定流水线 $0.0151 的 220% · agent 课件题 $0.027 的 123%

误报待人工复核：
- examples_more_fewer.a:clean｜not_found｜这些方向就是协方差矩阵的特征向量，对应的特征值大小表示数据沿该方向伸展的程度
- examples_more_fewer.a:clean｜not_found｜特征值最大的方向就是数据变化最剧烈的主方向
- examples_more_fewer.a:clean｜not_found｜PCA就是把协方差矩阵对角化，把这些伸展方向按重要性排出来
- depth_what_why.b:clean｜not_found｜第2页只给出了一般定义 Av=λv，没有专门讨论λ=0的情况
