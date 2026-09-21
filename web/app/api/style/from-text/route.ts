import type { Arm } from "@/lib/style/axes";
import { NextResponse } from "next/server";

import {
  AXES,
  AXIS_IDS,
  AXIS_LABEL,
  VALUE_HINT,
  VALUE_LABEL,
  isAxisValue,
  type AxisId,
  type StyleConfig,
} from "@/lib/style/axes";
import { recordEvent, textMapped } from "@/lib/style/events";
import { GEMINI_ENDPOINT, TEXT_ARM_CALL } from "@/lib/style/text-arm";
import { createClient } from "@/lib/supabase/server";

/**
 * 对照臂（A5 的 `text` 臂）：一段文字描述 → 四条风格轴。
 *
 * ## 为什么必须是服务端
 *
 * 模型调用要 `ANTHROPIC_API_KEY`。它**不带 `NEXT_PUBLIC_` 前缀**，所以不会被内联进
 * bundle；构建期护栏（`lib/public-env-guard.mjs`）只扫 `NEXT_PUBLIC_*`，正是为了让
 * 服务端密钥有地方放——一条会在阶段 3 变成障碍的护栏迟早会被关掉。
 *
 * 连带后果（已登记）：**Vercel 从「只做渲染」变成处理者**，用户输入的文字会经
 * Vercel(SG) 出境到 Anthropic。PRD §13.3 的出境清单与隐私政策要相应改写。
 *
 * ## 这个端点只分类，不生成讲解（2026-09-09 拍板）
 *
 * 不现场生成一段讲解给用户看，四条理由与口径 F02 同源：**合规**（§12.1 要求对外展示
 * 一律用自写合成课件）· **成本**（每新用户 ≈$0.18，发生在用户产生任何价值之前）·
 * **延迟**（首屏不能等一次生成）· **对照纪律**（现场生成的东西没人预先核过）。
 *
 * ## 三条防线
 *
 * 1. **要登录**：未登录直接 401，不调模型。没有这条，拿到 URL 的人就能烧我们的额度。
 * 2. **要在 `text` 臂**：主臂用户不该走到这里，走到了就是前端 bug——**报 409，不静默放行**。
 * 3. **限长**：`MAX_CHARS` 是成本上界，不是体验偏好。超了报 400 并说明。
 *
 * ## `style_text_mapped` 事件为什么写在服务端（阶段 4）
 *
 * 两个理由：
 *
 * 1. **`call` 只有这里知道是真的。** 客户端拿到的 `call` 是响应体里的一份拷贝，
 *    由这里发出去；让客户端回填事件，等于让被记录方自己报告自己用了什么。
 * 2. **失败也要有记录。** 模型跑偏、限流、返回非法取值这些分支根本走不到客户端的
 *    成功路径，而失败率恰恰是这条臂最该看的数字之一（分类质量至今没有金标准，
 *    见 F1进度 §五「已知未控制」）。
 *
 * **事件里没有描述原文**：`textMapped()` 压根不接受这个参数（见 `lib/style/events.ts`）。
 * 用「记得别传」来保证是不行的，参数里没有这个东西才是真的传不进来。
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_CHARS = 800;

/** 8-4-4-4-12 十六进制。只判形状，不判版本位——它是我们自己生成的。 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 每条轴的取值 + 一句依据；`unspecified` = 描述里没提到这一维。 */
const UNSPECIFIED = "unspecified";

/**
 * ⚠️ **不写 `additionalProperties`。** Gemini 的 `responseSchema` 只认 OpenAPI 的
 * 一个子集，多给一个字段它会整条请求 400，而那个 400 读起来像「参数写错了」。
 * 取值域靠 `enum` 管住，多出来的字段由下面的取值域校验挡下——
 * 那一层本来就要有（模型跑偏不是假设，是已经发生过的事）。
 */
function axisSchema(axis: AxisId) {
  return {
    type: "object",
    properties: {
      value: { type: "string", enum: [...AXES[axis], UNSPECIFIED] },
      reason: { type: "string", description: "一句话说明依据；未提到就说明为什么算未提到" },
    },
    required: ["value", "reason"],
  };
}

const SCHEMA = {
  type: "object",
  properties: Object.fromEntries(AXIS_IDS.map((axis) => [axis, axisSchema(axis)])),
  required: [...AXIS_IDS],
};

/**
 * 轴的说明取自 `lib/style/axes.ts` 的同一份文案，**不在这里另写一遍**。
 * 页面上写给用户看的解释和喂给模型的定义漂开，是最难查的一类不一致：
 * 用户按页面上的话描述，模型按另一套定义分类，两边都「没错」。
 */
function buildSystem(): string {
  const axes = AXIS_IDS.map((axis) => {
    const values = (AXES[axis] as readonly string[])
      .map((v) => {
        const label = (VALUE_LABEL[axis] as Record<string, string>)[v];
        const hint = (VALUE_HINT[axis] as Record<string, string>)[v];
        return `  - \`${v}\`（${label}）：${hint}`;
      })
      .join("\n");
    return `- **${AXIS_LABEL[axis]}**（\`${axis}\`）\n${values}`;
  }).join("\n");

  return [
    "你在把用户对「AI 讲解课件时该怎么讲」的一段自由描述，映射到四条固定的风格轴上。",
    "",
    "四条轴与取值：",
    "",
    axes,
    "",
    "规则：",
    `1. 用户没有提到、也无法从描述稳妥推断的轴，取值填 \`${UNSPECIFIED}\`。**不要猜**——`,
    "   猜出来的偏好和用户真实的偏好在库里长得一模一样，之后没人分得开。",
    "2. 只依据用户这段描述判断，不要依据你对「好讲解」的看法。",
    "3. `reason` 用一句中文写清依据，尽量引用用户的原话。",
    "4. 用户描述里与这四条轴无关的内容（比如语言、篇幅总量、学科），忽略即可。",
  ].join("\n");
}

/**
 * 这把 key 能用、且支持 `generateContent` 的模型有哪些。
 *
 * **只在 404 那一支调**——它是为了把「那该用哪个」当场答掉，不是常规路径的一环。
 * 自己失败就返回空数组：这时候用户已经在看一个错误了，再叠一个错误没有意义，
 * 而空数组会让上面那句话改口说「列不出来，多半是 key 或 API 没启用」——
 * **那也是一条有用的信息**，不是沉默。
 */
async function listModels(apiKey: string): Promise<string[]> {
  try {
    const r = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(apiKey)}&pageSize=200`,
      { cache: "no-store" },
    );
    if (!r.ok) return [];
    const body = (await r.json()) as {
      models?: { name?: string; supportedGenerationMethods?: string[] }[];
    };
    return (body.models ?? [])
      .filter((m) => (m.supportedGenerationMethods ?? []).includes("generateContent"))
      .map((m) => (m.name ?? "").replace(/^models\//, ""))
      .filter(Boolean);
  } catch {
    return [];
  }
}

type AxisOutcome = {
  value: string;
  source: "text" | "default";
  reason: string;
};

export async function POST(request: Request) {
  const supabase = await createClient();
  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) {
    return NextResponse.json({ error: "未登录" }, { status: 401 });
  }

  // 读**整行**：矫正要以用户当前的配置为基线（见下面 `base`）。
  const { data: rows, error: rowError } = await supabase
    .from("style_config")
    .select("f1_arm, detail_level, use_examples, structure, depth")
    .eq("user_id", auth.user.id);

  if (rowError) {
    return NextResponse.json({ error: `读取配置失败：${rowError.message}` }, { status: 500 });
  }
  if (!rows || rows.length !== 1) {
    return NextResponse.json(
      { error: `读到 ${rows?.length ?? 0} 行 style_config，应为 1 行（口径 F09）` },
      { status: 500 },
    );
  }
  // 2026-09-20：**臂的门禁去掉了。** 这个端点原来只给 `text` 对照臂用
  // （那条臂已退役），现在它服务的是所有人在讲解里的风格矫正。
  // 留着 409 的话，每个登录用户点「调整讲法」都会撞上一个说他「在成对比较臂」
  // 的错误——而那句话在成对比较撤掉之后已经没有意义了。
  const arm = rows[0].f1_arm as Arm;

  /**
   * 矫正的**基线是用户当前的配置**，不是默认档。
   *
   * ⚠️ 这是这次改动里最容易错的一条：用户说「再短一点」，描述里只提到了详细程度，
   * 另外三条轴没提。以默认档为基线的话，那三条会被**悄悄重置**——
   * 他改了一件事，结果四件事全变了，而页面上什么都不会说。
   */
  const base: StyleConfig = {
    detail_level: rows[0].detail_level,
    use_examples: rows[0].use_examples,
    structure: rows[0].structure,
    depth: rows[0].depth,
  } as StyleConfig;

  let description: unknown;
  let sessionId: unknown;
  try {
    const body = await request.json();
    description = body?.description;
    sessionId = body?.session_id;
  } catch {
    return NextResponse.json({ error: "请求体不是合法 JSON" }, { status: 400 });
  }

  // session_id 由页面在**服务端**生成后传给客户端组件，客户端原样带回来。
  // 这里只校验形状：拿到一个不是 uuid 的东西说明前端接线断了，
  // **判失败而不是补一个新的**——补一个新的会让这次调用挂在一个凭空出现的
  // session 上，而事后没有任何东西指得出它是补的（同 F32「回落必须显式」）。
  if (typeof sessionId !== "string" || !UUID_RE.test(sessionId)) {
    return NextResponse.json(
      { error: `session_id 不是合法 uuid：${JSON.stringify(sessionId)}` },
      { status: 400 },
    );
  }

  /** 事件写库。失败只记日志，不改变这次请求的结果——采集不该打断用户。 */
  const track = async (event: ReturnType<typeof textMapped>) =>
    (await recordEvent(supabase, { userId: auth.user.id, arm, sessionId, event })).ok;

  if (typeof description !== "string" || description.trim().length === 0) {
    return NextResponse.json({ error: "描述为空" }, { status: 400 });
  }
  if (description.length > MAX_CHARS) {
    return NextResponse.json(
      { error: `描述 ${description.length} 字，超过上限 ${MAX_CHARS} 字` },
      { status: 400 },
    );
  }

  const apiKey = process.env.GEMINI_API_KEY?.trim();
  if (!apiKey) {
    // 缺 key 是部署配置问题，不是用户输入问题——报 503 并说清是服务端的事。
    return NextResponse.json(
      { error: "服务端未配置 GEMINI_API_KEY，「说一句想要的讲法」暂时不可用" },
      { status: 503 },
    );
  }

  let raw: string;
  try {
    // 参数**全部**取自 TEXT_ARM_CALL：发出去的和记进事件的必须是同一份。
    //
    // 用 fetch 直接打 REST，不引 SDK：这里只需要一次 generateContent，
    // 而多一个依赖就多一份要跟着升级的东西（何况 bundle 也要装它）。
    const r = await fetch(`${GEMINI_ENDPOINT}?key=${encodeURIComponent(apiKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: buildSystem() }] },
        contents: [{ role: "user", parts: [{ text: description }] }],
        generationConfig: {
          responseMimeType: "application/json",
          responseSchema: SCHEMA,
          temperature: TEXT_ARM_CALL.temperature,
          maxOutputTokens: TEXT_ARM_CALL.max_output_tokens,
        },
      }),
    });

    if (!r.ok) {
      // 失败分支各记一条 `ok: false` 的事件。**`failure` 是分类不是文案**——
      // 记一句人话的话，事后要按正则去分组，而正则会随文案漂。
      const body = await r.text().catch(() => "");
      if (r.status === 429) {
        await track(textMapped({ ok: false, failure: "rate_limited" }));
        return NextResponse.json(
          { error: "这一分钟问得有点多（免费额度约 10 次/分钟），过一会儿再试。" },
          { status: 429 },
        );
      }
      if (r.status === 401 || r.status === 403) {
        await track(textMapped({ ok: false, failure: "bad_credentials" }));
        return NextResponse.json({ error: "服务端的模型凭据无效" }, { status: 503 });
      }
      if (r.status === 404) {
        // **点名那个模型 id，并且把「那该用哪个」一起答了。**
        //
        // 2026-09-21：只点名不够。用户看到「模型 X 不存在，可用 GEMINI_MODEL
        // 换一个」之后，下一个问题必然是「换成什么」——而那个答案只有 Google
        // 知道，猜第二个 id 只是把同一轮再走一遍。
        //
        // 所以这一支顺手问一次 ListModels，把**这把 key 真的能用**的模型列出来。
        // 只在 404 这一支发生，正常路径一次都不多调。
        await track(textMapped({ ok: false, failure: "api_error" }));
        const available = await listModels(apiKey);
        // 也进服务端日志：UI 里那串可能被截断，而 Vercel 的日志是部署当场就能翻的。
        console.error(JSON.stringify({
          event: "gemini_model_not_found",
          tried: TEXT_ARM_CALL.model,
          available,
        }));
        return NextResponse.json(
          {
            error:
              `模型 ${TEXT_ARM_CALL.model} 对这把 key 不可用。` +
              (available.length > 0
                ? `这把 key 能用的是：${available.join("、")}。` +
                  `把其中一个填进 Vercel 的 GEMINI_MODEL 再重新部署一次。`
                : `而且列不出可用模型（${available.length === 0 ? "返回为空" : ""}）——` +
                  `多半是这把 key 本身有问题，或者 Generative Language API 还没启用。`),
            available_models: available,
          },
          { status: 502 },
        );
      }
      await track(textMapped({ ok: false, failure: "api_error" }));
      console.error(JSON.stringify({ event: "gemini_http_error", status: r.status, body: body.slice(0, 400) }));
      return NextResponse.json({ error: `模型调用失败（HTTP ${r.status}）` }, { status: 502 });
    }

    const payload = (await r.json()) as {
      candidates?: { content?: { parts?: { text?: string }[] }; finishReason?: string }[];
      promptFeedback?: { blockReason?: string };
    };

    if (payload.promptFeedback?.blockReason) {
      await track(textMapped({ ok: false, failure: "refusal" }));
      return NextResponse.json(
        { error: "模型拒绝处理这段描述，请换一种说法" },
        { status: 422 },
      );
    }

    const candidate = payload.candidates?.[0];
    if (candidate?.finishReason === "MAX_TOKENS") {
      await track(textMapped({ ok: false, failure: "max_tokens" }));
      return NextResponse.json({ error: "模型输出被截断，请重试" }, { status: 502 });
    }

    const text = candidate?.content?.parts?.map((x) => x.text ?? "").join("") ?? "";
    if (!text.trim()) {
      await track(textMapped({ ok: false, failure: "no_text_block" }));
      return NextResponse.json({ error: "模型没有返回文本" }, { status: 502 });
    }
    raw = text;
  } catch (err) {
    // fetch 抛出来只有一类：连不上。状态码在上面那一支里判。
    await track(textMapped({ ok: false, failure: "connection" }));
    console.error(JSON.stringify({ event: "gemini_connection_error", detail: String(err) }));
    return NextResponse.json({ error: "连不上模型服务，请稍后重试" }, { status: 504 });
  }

  let parsed: Record<string, { value?: unknown; reason?: unknown }>;
  try {
    parsed = JSON.parse(raw);
  } catch {
    await track(textMapped({ ok: false, failure: "not_json" }));
    return NextResponse.json({ error: "模型返回的不是合法 JSON" }, { status: 502 });
  }

  // 服务端按取值域校验。**非法取值判失败，不改写成默认值**（同 F10：不静默改写）——
  // 改写会把「模型跑偏了」伪装成「用户没提这一维」，而这两件事的处置完全不同。
  const axes: Record<string, AxisOutcome> = {};
  // **从用户当前的配置起步**（不是默认档，见上面 `base`）：没提到的轴原样保留，
  // 提到的覆盖掉。这样 `config` 全程是一个完整的 StyleConfig，
  // 不存在「少一条轴」的中间态。
  const config: StyleConfig = { ...base };

  for (const axis of AXIS_IDS) {
    const entry = parsed?.[axis];
    const value = entry?.value;
    const reason = typeof entry?.reason === "string" ? entry.reason : "";

    if (value === UNSPECIFIED) {
      // `source: "default"` 这个名字是**历史的**：它现在的含义是
      // 「这一轴描述里没提到，所以保持原样」，而不是「回落到默认档」。
      // 名字没改是因为它已经进了 `style_text_mapped` 事件的必需字段
      // （改名要连迁移与两处镜像一起动）。**登记为待清理**，
      // 在那之前读这个字段的人要知道它说的是「没提到」，不是「用了默认值」。
      axes[axis] = { value: base[axis], source: "default", reason };
      continue;
    }
    if (!isAxisValue(axis, value)) {
      await track(textMapped({ ok: false, failure: "value_out_of_domain" }));
      return NextResponse.json(
        {
          error: `模型给出的 ${axis} 取值不在取值域内：${JSON.stringify(value)}`,
        },
        { status: 502 },
      );
    }
    axes[axis] = { value, source: "text", reason };
    // 上一行的 `isAxisValue` 已按轴校验过取值域；这里的断言只是为了绕开
    // 「联合类型的键 + 联合类型的值」在赋值处收窄成 never 的那个老问题。
    (config as Record<string, string>)[axis] = value;
  }

  // 哪几条轴来自描述、哪几条回落默认（口径 F32：回落必须显式）。
  // 存成两个列表而不是一个 map，是因为分析时问的就是「几条来自描述」——
  // 存 map 的话每次都要在查询里数一遍。
  const axesFromText = AXIS_IDS.filter((a) => axes[a]?.source === "text");
  const axesFromDefault = AXIS_IDS.filter((a) => axes[a]?.source === "default");

  const written = await track(
    textMapped({ ok: true, axesFromText, axesFromDefault }),
  );

  // 结构化日志保留：事件表要靠 SQL 才看得到，而 Vercel 日志是部署当场就能看的
  // 那一份（人工验收清单里靠它核对 call 与 text-arm.ts 一致）。
  // **仍然不记描述原文**——那是用户内容（PRD §12.2）。
  // `event_written` 让「事件没写进去」与「我查错了地方」在日志里就能分开。
  console.log(
    JSON.stringify({
      event: "style_text_mapped",
      call: TEXT_ARM_CALL,
      ok: true,
      axes_from_text: axesFromText,
      axes_from_default: axesFromDefault,
      event_written: written,
    }),
  );

  return NextResponse.json({ config, axes, call: TEXT_ARM_CALL });
}
