import Anthropic from "@anthropic-ai/sdk";
import { NextResponse } from "next/server";

import {
  AXES,
  AXIS_IDS,
  AXIS_LABEL,
  DEFAULTS,
  VALUE_HINT,
  VALUE_LABEL,
  isAxisValue,
  type AxisId,
  type StyleConfig,
} from "@/lib/style/axes";
import { TEXT_ARM_CALL } from "@/lib/style/text-arm";
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
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_CHARS = 800;

/** 每条轴的取值 + 一句依据；`unspecified` = 描述里没提到这一维。 */
const UNSPECIFIED = "unspecified";

function axisSchema(axis: AxisId) {
  return {
    type: "object",
    properties: {
      value: { type: "string", enum: [...AXES[axis], UNSPECIFIED] },
      reason: { type: "string", description: "一句话说明依据；未提到就说明为什么算未提到" },
    },
    required: ["value", "reason"],
    additionalProperties: false,
  };
}

const SCHEMA = {
  type: "object",
  properties: Object.fromEntries(AXIS_IDS.map((axis) => [axis, axisSchema(axis)])),
  required: [...AXIS_IDS],
  additionalProperties: false,
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

  const { data: rows, error: rowError } = await supabase
    .from("style_config")
    .select("f1_arm")
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
  if (rows[0].f1_arm !== "text") {
    return NextResponse.json(
      { error: "该账号在成对比较臂，不该调用文字描述接口" },
      { status: 409 },
    );
  }

  let description: unknown;
  try {
    description = (await request.json())?.description;
  } catch {
    return NextResponse.json({ error: "请求体不是合法 JSON" }, { status: 400 });
  }

  if (typeof description !== "string" || description.trim().length === 0) {
    return NextResponse.json({ error: "描述为空" }, { status: 400 });
  }
  if (description.length > MAX_CHARS) {
    return NextResponse.json(
      { error: `描述 ${description.length} 字，超过上限 ${MAX_CHARS} 字` },
      { status: 400 },
    );
  }

  const apiKey = process.env.ANTHROPIC_API_KEY?.trim();
  if (!apiKey) {
    // 缺 key 是部署配置问题，不是用户输入问题——报 503 并说清是服务端的事。
    return NextResponse.json(
      { error: "服务端未配置 ANTHROPIC_API_KEY，文字描述暂时不可用" },
      { status: 503 },
    );
  }

  const client = new Anthropic({ apiKey });

  let raw: string;
  try {
    // 参数**全部**取自 TEXT_ARM_CALL：发出去的和记进事件的必须是同一份，
    // 否则事后从数据里看不出两条臂的对照关系什么时候变的。
    const message = await client.messages.create({
      model: TEXT_ARM_CALL.model,
      max_tokens: TEXT_ARM_CALL.max_tokens,
      thinking: { type: TEXT_ARM_CALL.thinking },
      output_config: {
        effort: TEXT_ARM_CALL.effort,
        format: { type: "json_schema", schema: SCHEMA },
      },
      system: buildSystem(),
      messages: [{ role: "user", content: description }],
    });

    if (message.stop_reason === "max_tokens") {
      return NextResponse.json({ error: "模型输出被截断，请重试" }, { status: 502 });
    }
    if (message.stop_reason === "refusal") {
      return NextResponse.json(
        { error: "模型拒绝处理这段描述，请换一种说法" },
        { status: 422 },
      );
    }

    const text = message.content.find((b) => b.type === "text");
    if (!text || text.type !== "text") {
      return NextResponse.json({ error: "模型没有返回文本" }, { status: 502 });
    }
    raw = text.text;
  } catch (err) {
    if (err instanceof Anthropic.RateLimitError) {
      return NextResponse.json({ error: "模型调用被限流，请稍后重试" }, { status: 429 });
    }
    if (err instanceof Anthropic.AuthenticationError) {
      return NextResponse.json({ error: "服务端的模型凭据无效" }, { status: 503 });
    }
    if (err instanceof Anthropic.APIConnectionError) {
      return NextResponse.json({ error: "连不上模型服务，请稍后重试" }, { status: 504 });
    }
    const detail = err instanceof Anthropic.APIError ? `（${err.status}）` : "";
    return NextResponse.json({ error: `模型调用失败${detail}` }, { status: 502 });
  }

  let parsed: Record<string, { value?: unknown; reason?: unknown }>;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return NextResponse.json({ error: "模型返回的不是合法 JSON" }, { status: 502 });
  }

  // 服务端按取值域校验。**非法取值判失败，不改写成默认值**（同 F10：不静默改写）——
  // 改写会把「模型跑偏了」伪装成「用户没提这一维」，而这两件事的处置完全不同。
  const axes: Record<string, AxisOutcome> = {};
  // 从默认值起步：没提到的轴保持默认，提到的覆盖掉。
  // 这样 `config` 全程是一个完整的 StyleConfig，不存在「少一条轴」的中间态。
  const config: StyleConfig = { ...DEFAULTS };

  for (const axis of AXIS_IDS) {
    const entry = parsed?.[axis];
    const value = entry?.value;
    const reason = typeof entry?.reason === "string" ? entry.reason : "";

    if (value === UNSPECIFIED) {
      axes[axis] = { value: DEFAULTS[axis], source: "default", reason };
      continue;
    }
    if (!isAxisValue(axis, value)) {
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

  // `call` 回给客户端，是为了让阶段 4 的 `style_text_mapped` 事件能**原样**带上
  // 模型与参数字符串。它一变，两条臂的对照关系就变了，而事后从数据里看不出来。
  //
  // 事件表要到阶段 4 才有，所以现在先在服务端留一条结构化日志——
  // **不记描述原文**（那是用户内容），只记这次调用用了什么。
  console.log(
    JSON.stringify({ event: "style_text_mapped", call: TEXT_ARM_CALL, ok: true }),
  );

  return NextResponse.json({ config, axes, call: TEXT_ARM_CALL });
}
