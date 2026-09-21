/**
 * 「一段话 → 四条风格轴」那次模型调用的**唯一出处**：模型 id 与采样参数。
 *
 * ## 为什么单独拎出来
 *
 * 这几个值**同时是两件事**：真正发出去的请求参数，和要落进埋点的记录。
 * 写成两份（一份传给 SDK、一份写进事件）必然漂——而漂掉之后
 * **事后从数据里看不出来**：事件里记着一个模型，实际发的是另一个，
 * 却没有任何东西会红。所以请求参数、响应里回给客户端的、事件字段，
 * 全部从这一个对象取。一处改，三处一起变。
 *
 * ## 2026-09-20：换成 Gemini Flash，用途也换了
 *
 * **用途变了**：原来它服务的是 A5 的 `text` 对照臂（新用户开场描述自己的偏好）。
 * 那条臂已经退役——前提「用户能描述出自己的偏好」被否掉了。
 * 现在它服务的是**讲解里的风格矫正**：用户看过一段真实讲解之后说
 * 「再短一点」「多举个例子」，这时他手上有具体的东西可以反应。
 *
 * **模型换了**：原来是 `claude-sonnet-5`，与生产讲解同一个模型。矫正是一次
 * 极短的分类，用生产模型是在为一次分类付长文生成的价。Gemini Flash 的免费额度
 * （约 10 次/分钟）对个人测试版足够，而这个项目明确「不投钱」。
 *
 * ⚠️ **代价如实登记**：多了一个服务商。PRD §12.3 的服务商查证本来就卡着，
 * 现在多一项；出境清单（§13.3）也多一条——用户写的那句风格描述会到 Google。
 * 那句话**不进事件**（`textMapped()` 压根不接受这个参数），但它确实出境了。
 *
 * ⚠️ 口径 F19 不管这个调用。F19 约束的是「样例的整条请求取生产侧唯一出处」，
 * 因为样例必须等于用户真会拿到的输出；这次调用不产出任何给用户看的讲解。
 */
export const TEXT_ARM_CALL = {
  provider: "google",
  /**
   * ⚠️ **可以用环境变量覆盖**（`GEMINI_MODEL`）。写死一个 id 的风险是它被下线之后
   * 整条矫正静默不可用——而 Google 返回的 404 会被我们翻译成「模型调用失败」，
   * 看起来像网络问题。改一个环境变量比改代码再部署快得多。
   *
   * ## 默认值猜错过**两次**，所以现在不猜带版本号的 id 了
   *
   * 2026-09-21：`gemini-2.5-flash` → 404；换 `gemini-2.0-flash` → 404，
   * 而 ListModels 报回来的阵容是 2.5 / 3.x / 3.5–3.8——**那一代编号早就换过了**。
   * 根因不是「挑错了型号」，是**拿训练数据里的版本号当事实**。
   *
   * 第一反应是换成别名 `gemini-flash-latest`：它跟着 Google 走，不会因为某个编号
   * 退役而失效。**这一步也错了**，而且错得更有意思：
   *
   * 用户 AI Studio 的额度页显示，那个别名解析到了 **Gemini 3.8 Flash**——
   * 最新的那一个，免费层 **5 RPM / 20 RPD**，而且正因为新所以最挤（连着 503）。
   * **「最新」对一次后台分类来说是最差的选择**：配额最紧、争用最凶，
   * 而我们要的只是把一句话映射到四条轴。
   *
   * 所以最终默认 `gemini-flash-lite-latest`：**lite 那一支的别名**。
   * 仍然是别名（躲开版本号过时），但指向的是为「便宜、量大」设计的那一档。
   *
   * ⚠️ 别名指向谁**不再靠猜**：响应里的 `modelVersion` 会被记进日志与响应，
   * 「我这次实际用的是哪个模型」是一个查得出来的事实。
   *
   * 真正的答案仍然不靠这一行：route 在 404 那一支会问一次 ListModels，
   * 把**这把 key 真的能用**的模型列进错误信息。这一行只决定运气好时不用走那一步。
   */
  model: process.env.GEMINI_MODEL?.trim() || "gemini-flash-lite-latest",
  /** 分类任务要稳定：同一句话两次映射到不同的轴，用户会以为是随机的。 */
  temperature: 0,
  max_output_tokens: 1500,
} as const;

export type TextArmCall = typeof TEXT_ARM_CALL;

/**
 * API 版本。**也做成可配的**（`GEMINI_API_VERSION`）。
 *
 * 2026-09-21 撞到一个解释不了的现象：`gemini-2.5-flash` 在 ListModels（v1beta）
 * 里列得出来，但对它调 `:generateContent` 返回 404。最可能的解释是这个模型的
 * generateContent 已经不在 v1beta 上了——而**这件事我没法在本地验证**（没有 key）。
 *
 * 与其再猜一次，不如把版本也变成一个改环境变量就能试的东西：
 * 下一次撞到 404，换 `v1` 再试一次比改代码再部署快得多。
 */
export const GEMINI_API_VERSION = process.env.GEMINI_API_VERSION?.trim() || "v1beta";

/** Gemini 的 REST 端点。key 走查询参数（Google 的标准用法）。 */
export const GEMINI_ENDPOINT =
  `https://generativelanguage.googleapis.com/${GEMINI_API_VERSION}` +
  `/models/${TEXT_ARM_CALL.model}:generateContent`;

/** ListModels。与上面同一个版本——**列出来的和调用的必须是同一个 API 版本**，
 *  否则「列表里有它却调不通」这种现象会一直说不清楚。 */
export const GEMINI_LIST_ENDPOINT =
  `https://generativelanguage.googleapis.com/${GEMINI_API_VERSION}/models`;
