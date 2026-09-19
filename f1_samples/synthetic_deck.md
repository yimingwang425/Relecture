<!--
自写合成内容，非第三方课件，用于 F1 样例。

作者：本项目（2026-09-07 Bloom filter 初版 · 2026-09-19 换成线性代数）。
不含任何第三方课件原文或其改写。PRD §12.1：对外展示一律使用自写的合成课件。

## 主题：eigenvalues and eigenvectors（特征值与特征向量）

2026-09-19 换题的理由：onboarding 是用户**第一次**打开产品看到的东西，
他要在两段讲解之间选一个。旧题 Bloom filter 需要先懂哈希与误判率，
不熟的人会去读**内容**而不是比**讲法**——那正好打穿 F01 想要的归因。
线性代数（矩阵、特征值）是大学通识，几乎人人学过，注意力才留得在讲法上。

仍然避开 22.081J（能源/核工程）与 14.129（博弈论/机制设计）。

## 页与块

每页 2–3 个**块**。块 id 写在注释里，`generate_pairs.py` 按 id 取用，
不靠行号或正则去猜。英文课件 + 中文讲解，与生产一致（真实课件多为英文讲义）。

## 规模仍对齐实测（不是靠少喂来缩短样例）

这一版 5 块各 250–393 字符、合计 1427，落在实测四分位 1128–1498 内
（真实每轮恒 5 片、单片中位 271、范围 60–433）。旧版合计 1495，同样在范围内。

**样例变短要靠题目本身，不靠少喂**：少喂会让样例不再代表用户真会拿到的东西，
那正是 F18 禁止的。特征值这一题的内容密度低于 Bloom filter（无误判率推导、
无参数选择），模型在同一份风格配置下自然写得短一些；到底短多少以生成结果为准，
不在这里预告。

## 喂哪 5 块（口径 F28：我在扮演检索器）

**可选块池**（6 块）：`p1b1` 矩阵即线性映射 · `p2b1` 特征值定义 ·
`p3b1` 三个能直接读出特征值的例子 · `p4b1` 反复作用时主方向主导 ·
`p5b2` 对称矩阵 · `p7b1` 两处真实应用

⚠️ **2026-09-19 改成每对只喂 1–2 块**（不再是每对都喂同样的 5 块）。两个理由：

1. 8 对全用同一个问题、同一批片段时，用户连着读 12 段几乎一样的内容——
   **选了跟没选没区别**，而 F01 要的归因正建立在「他读出了差异」上。
   现在每对一个不同的短问题，各自取自己那 1–2 块。
2. 5 块 → 1–2 块，输出自然变短。**不靠「请简短」这类指令**：那会让 prompt 形状
   与生产分叉，F19 的前提就垮了。

代价如实登记：**样例比真实一轮短**（真实每轮恒 5 片）。方向单向、已知；
换来的是用户真的会读完两段再选。

2026-09-19 换过一次 FED 集合：原来喂 `p5b2`（对称矩阵）。实测 P3（每段 15–40 字）
连续 6 次都在 43–69 字，从未进区间——那一块抽象、可枚举的事实少，模型只能写长段。
换成 `p3b1` 的三个具体例子（镜线 1 / 垂直方向 −1 / 整体放大 2 / 零特征值）之后再测。
**判据一个数都没动**，动的是喂进去的内容。

**FED 块的两条准入条件**（换素材时逐块过一遍）：

1. **页间无前向引用**——不提任何只在不喂的页上解释的东西。
   行列式、特征多项式 det(A−λI)=0、2×2 算例、复特征值、对角化 A=PDP⁻¹、
   幂法与 QR 算法，全部在不喂的页上，FED 块一个字都不提。
2. **块内无悬空前提**——不承接未喂块的假设、指代或符号。
   每个 FED 块自己交代它用到的符号：`A` 是方阵、`v` 是非零向量、`λ` 是标量，
   在 p2b1 一次说全；p4b1 与 p5b2 各自重述自己需要的前提，不指望读者记得上一块。
-->

# Linear Algebra · Eigenvalues and Eigenvectors

---

## Page 1 · What a matrix does to a vector

<!-- p1b1 · FED -->
A square matrix `A` is a rule that takes a vector and returns another vector. Multiplying
by `A` can rotate a vector, stretch it, or do both at once. The useful question is not what
the entries of `A` are, but what `A` does to the vectors it acts on.

<!-- p1b2 -->
Two vectors of the same length can be added, and any vector can be scaled by a number.
A matrix respects both operations: it sends a sum to the sum of the images, and a scaled
vector to the scaled image. This is what makes the map linear rather than arbitrary.

---

## Page 2 · The definition

<!-- p2b1 · FED -->
Let `A` be a square matrix, `v` a nonzero vector, and `λ` a number. If `A v = λ v`, then
`v` is an eigenvector of `A` and `λ` is its eigenvalue. Most vectors are turned onto a new
line by `A`; an eigenvector keeps its own line and is only scaled, by the factor `λ`.

<!-- p2b2 -->
The scaling factor carries a sign. A negative eigenvalue flips the vector to point the
other way along the same line, which still counts as keeping the line. An eigenvalue of
zero means the matrix collapses that whole direction down to the origin.

---

## Page 3 · Reading eigenvalues off a picture

<!-- p3b1 · FED -->
A reflection across a line has two eigenvalues. Vectors along the mirror line are untouched,
so their eigenvalue is one. Vectors perpendicular to it are sent to their opposites, so their
eigenvalue is minus one. A pure scaling that doubles every vector has the single eigenvalue
two, and every direction is an eigenvector. An eigenvalue of zero means that direction is
collapsed to the origin.

<!-- p3b2 -->
A rotation of the plane by an angle that is not a multiple of a half turn leaves no line
fixed, so it has no eigenvector with a real eigenvalue. Page 6 says what happens if we
allow complex numbers, which is where the missing eigenvalues reappear.

---

## Page 4 · Why the fixed directions matter

<!-- p4b1 · FED -->
Suppose a system is updated by applying the same square matrix `A` over and over: today's
state is `A` times yesterday's. Along a direction that `A` only scales, each step multiplies
by that one factor, so after many steps the largest factor dominates what you see.

<!-- p4b2 -->
This is why the largest factor is often the only one anyone quotes. It decides whether
repeated application grows without bound, settles down, or dies away, and it does so
after enough steps regardless of the starting state.

---

## Page 5 · Symmetric matrices behave well

<!-- p5b1 -->
A matrix is symmetric when it equals its own transpose, that is, the entry in row i,
column j equals the entry in row j, column i. Covariance matrices and the matrices of
undirected graphs are symmetric by construction.

<!-- p5b2 · FED -->
Let `A` be a square matrix equal to its own transpose. Then every eigenvalue of `A` is a
real number, and eigenvectors with different eigenvalues meet at right angles. Such a matrix
stretches space along perpendicular directions, each by its own factor.

---

## Page 6 · When the eigenvalues are complex

<!-- p6b1 -->
Allowing complex numbers restores the eigenvalues of a rotation. A quarter turn of the
plane has eigenvalues i and minus i, and the pair encodes both the turning and the fact
that no real line survives. Complex eigenvalues always come in conjugate pairs.

<!-- p6b2 -->
For a real matrix, a complex eigenvalue and its conjugate describe a rotation combined
with a scaling by the modulus. The modulus is what decides growth or decay, exactly as
the absolute value of a real eigenvalue does.

---

## Page 7 · Where this is used

<!-- p7b1 · FED -->
Principal component analysis takes the covariance matrix of a data set and reports the
directions it stretches most, which are the axes the data varies along. Ranking algorithms
for linked pages look for the state that repeated updating leaves unchanged.

<!-- p7b2 -->
Structural engineers ask the same question of a bridge. The directions that a vibration
update leaves in place are the modes the structure prefers to move in, and the associated
factors say how fast each mode grows or decays under a periodic push.

---

## Page 8 · Finding them by hand

<!-- p8b1 -->
Eigenvalues are the numbers λ for which A − λI fails to be invertible, because that is
exactly when A v = λ v has a nonzero solution. Setting the determinant of A − λI to zero
gives the characteristic polynomial, whose roots are the eigenvalues.

<!-- p8b2 -->
For a 2×2 matrix the polynomial is quadratic, so the two eigenvalues follow from the
quadratic formula. The sum of the eigenvalues equals the trace, and their product equals
the determinant, which is a quick check on any hand computation.

---

## Page 9 · Diagonalization

<!-- p9b1 -->
If an n by n matrix has n independent eigenvectors, collect them as the columns of P and
the eigenvalues into a diagonal matrix D. Then A = P D P inverse, and powers of A cost
only powers of the diagonal entries.

<!-- p9b2 -->
Not every matrix can be written this way. A shear has a repeated eigenvalue with too few
independent eigenvectors, and no choice of P works; such matrices need the Jordan form
instead.

---

## Page 10 · How software does it

<!-- p10b1 -->
Nobody forms the characteristic polynomial numerically, because its roots are wildly
sensitive to rounding in its coefficients. Library routines use iterative methods, most
commonly the QR algorithm, which repeatedly factors and recombines the matrix.

<!-- p10b2 -->
The power method is the simplest iterative idea and follows page 4 directly: multiply a
starting vector by the matrix again and again, rescaling each time, and it converges to
the direction with the largest factor.
