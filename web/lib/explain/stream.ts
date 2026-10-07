/**
 * `POST /explain/stream` 的读法（口径 F94）。**纯模块**：不碰 DOM、不碰 fetch，
 * 所以 `verify:logic` 能直接断言它（与 `turns.ts` 同一个编译通道）。
 *
 * ## 三件事
 *
 * 1. **SSE 解析是增量的**：网络分包不认事件边界，一个 `data:` 行可能被拆成两半、
 *    一个包里也可能有三个事件。`createSseParser()` 攒着不完整的尾巴等下一包。
 * 2. **草稿只由事件推出来**：`applyStreamEvent()` 是一个归约函数——同一串事件
 *    重放两遍得到同一份草稿。`reset`（审查不达标、要重写）**清空正文**，
 *    否则用户看到的是两版拼在一起。
 * 3. **终态只有两个出口**：`done`（与 `/explain` 的响应体逐字段同形，交给
 *    `answerFromResponse()`）或 `error`（带 status，语义同状态码）。
 *    流在两者之前断掉，是第三种情况——**不是成功**，调用方要按失败处理。
 *
 * 决策日期：2026-10-07
 */

export type SseEvent = { type: string; data: Record<string, unknown> };

/**
 * 增量 SSE 解析器。`push()` 一包文本，返回这一包里**完整**的事件。
 *
 * 只认本服务用到的两种行：`event:` 与 `data:`；`:` 开头的注释行（保活）丢掉。
 * 认不出的 JSON **不吞**：原样包成 `{type: "error"}`，让页面说出来。
 */
export function createSseParser(): { push(chunk: string): SseEvent[] } {
  let buf = "";
  return {
    push(chunk: string): SseEvent[] {
      buf += chunk.replace(/\r\n/g, "\n");
      const out: SseEvent[] = [];
      let cut = buf.indexOf("\n\n");
      while (cut !== -1) {
        const block = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        let type: string | null = null;
        let raw: string | null = null;
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) type = line.slice(6).trim();
          else if (line.startsWith("data:")) raw = (raw ?? "") + line.slice(5).trimStart();
        }
        if (type !== null) {
          try {
            out.push({ type, data: raw ? (JSON.parse(raw) as Record<string, unknown>) : {} });
          } catch {
            out.push({ type: "error", data: { status: null, detail: `流里有一段读不懂的数据：${raw}` } });
          }
        }
        cut = buf.indexOf("\n\n");
      }
      return out;
    },
  };
}

/** 流式进行中的这一轮。**只活在 pending 期间**，终态由 `done` 的响应体决定。 */
export type StreamDraft = {
  stage: "connecting" | "retrieving" | "generating";
  /** 已经流出来的正文。`reset` 时清空。 */
  text: string;
  /** 审查判不达标、正在重写第几版（0 = 没在重写）。 */
  rewriting: number;
  /** 检索一完就有：出处先于正文给用户看。 */
  sources: { sourceFile: string; pages: number[] }[];
};

export const EMPTY_DRAFT: StreamDraft = { stage: "connecting", text: "", rewriting: 0, sources: [] };

/** 一个事件 → 下一份草稿。**纯函数**，不改入参。 */
export function applyStreamEvent(draft: StreamDraft, ev: SseEvent): StreamDraft {
  switch (ev.type) {
    case "stage": {
      const s = ev.data.stage;
      return s === "retrieving" || s === "generating" ? { ...draft, stage: s } : draft;
    }
    case "retrieval": {
      const slices = ((ev.data.retrieval as { slices?: unknown[] } | undefined)?.slices ?? []) as {
        source_file: string;
        pages?: number[];
      }[];
      const byFile = new Map<string, Set<number>>();
      for (const s of slices) {
        const set = byFile.get(s.source_file) ?? new Set<number>();
        for (const p of s.pages ?? []) set.add(p);
        byFile.set(s.source_file, set);
      }
      return {
        ...draft,
        sources: [...byFile.entries()].map(([sourceFile, pages]) => ({
          sourceFile,
          pages: [...pages].sort((a, b) => a - b),
        })),
      };
    }
    case "delta":
      return { ...draft, stage: "generating", text: draft.text + String(ev.data.text ?? "") };
    case "reset":
      // 上一版已经给用户看过了，审查说不达标 → **清掉**，不在后面接着写。
      return { ...draft, text: "", rewriting: Number(ev.data.attempt ?? draft.rewriting + 1) };
    default:
      return draft;
  }
}

export type StreamOutcome =
  | { kind: "done"; response: Record<string, unknown> }
  | { kind: "error"; status: number | null; detail: string }
  | { kind: "cut"; detail: string };

/**
 * 读完一条流。每来一个事件就把新草稿交给 `onDraft`；返回终态。
 *
 * `cut`：流在 `done` / `error` 之前就断了（网络掉线、服务端崩了、代理掐了连接）。
 * **它不是成功**——哪怕草稿里已经有大半段正文。服务端那一轮多半仍会跑完并落库
 * （见 `api/api/main.py` 的 `explain_stream`），刷新会话就能看到完整的那一版。
 */
export async function readExplainStream(
  body: ReadableStream<Uint8Array>,
  onDraft: (d: StreamDraft) => void,
): Promise<StreamOutcome> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();
  let draft = EMPTY_DRAFT;
  for (;;) {
    const { value, done } = await reader.read();
    const events = parser.push(done ? decoder.decode() : decoder.decode(value, { stream: true }));
    for (const ev of events) {
      if (ev.type === "done") return { kind: "done", response: ev.data.response as Record<string, unknown> };
      if (ev.type === "error") {
        return {
          kind: "error",
          status: typeof ev.data.status === "number" ? ev.data.status : null,
          detail: String(ev.data.detail ?? "服务端没有说明原因"),
        };
      }
      draft = applyStreamEvent(draft, ev);
      onDraft(draft);
    }
    if (done) return { kind: "cut", detail: "连接在讲解结束前断开了" };
  }
}
