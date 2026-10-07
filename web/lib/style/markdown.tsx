/**
 * 样例正文的极小 markdown 渲染。**只覆盖样例里真实出现的语法。**
 *
 * 实测 16 个样例（`pairs.json`）用到的全部标记：
 * `**粗体**` 191 处 · `` `代码` `` 375 处 · `- ` 列表 66 行 · `#` 标题 59 行 ·
 * `1. ` 有序列表 29 行 · `> ` 引用 22 行 · `*斜体*` 3 处 · 表格 0 处。
 *
 * 三个决定：
 *
 * 1. **不引 markdown 库**。上面这张表就是全部需求，`react-markdown` + `remark`
 *    进 bundle 换来的是 99% 用不到的语法，以及一条新的供应链。
 * 2. **不用 `dangerouslySetInnerHTML`**。样例是仓库里的静态资产，不是用户输入，
 *    但渲染路径一旦接受 HTML，下次有人往这里塞别的东西时就没有第二道防线了。
 *    这里直接产出 React 节点。
 * 3. **单个 `*` 按斜体处理，但这是量过之后才敢做的**。起初担心它会吃掉
 *    `k*n` 这类数学表达，于是打算原样输出；实际数了一遍：16 个样例里的
 *    单星共 6 个，**全部是 3 对配对的斜体**，各自落在同一个自然段内且不跨行；
 *    公式里的乘号一律写在反引号里（`` `k*n` ``），而代码分支在下面的
 *    交替里排在斜体之前，先把整段代码吃掉，斜体规则根本看不到里面的星号。
 *    **认不出的标记仍然按原样输出**——猜错语义比渲染难看更危险。
 */

import type { ReactNode } from "react";

/**
 * `**粗体**` · `` `代码` `` · `*斜体*`。三个都不跨行。
 *
 * **顺序有意义**：代码排在斜体之前，所以 `` `k*n` `` 会被整段当成代码吃掉，
 * 里面的星号不会被斜体规则看到。斜体两侧的 `(?<!\*)` / `(?!\*)` 保证它不去咬
 * `**粗体**` 的星号。
 */
const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|(?<!\*)\*(?!\*)[^*\n]+\*(?!\*))/g;

function inline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (
      part.startsWith("*") &&
      part.endsWith("*") &&
      !part.startsWith("**") &&
      part.length > 2
    ) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={key} className="rounded bg-[var(--c-code-bg)] px-1 py-0.5 font-mono text-[0.9em]">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={key}>{part}</span>;
  });
}

type Block =
  | { kind: "p" | "quote" | "h"; lines: string[] }
  | { kind: "ul" | "ol"; items: string[] };

/**
 * 按空行与行首标记切块。同类相邻行合并，段内换行接起来（中文段落不需要硬换行）。
 *
 * ⚠️ **空行结束一个自然段**（2026-10-07 修）。原实现遇到空行只是 `continue`，
 * 下一行又落进 `last.kind === "p"` 分支被并进上一段——这段注释一直写着「按空行切块」，
 * 代码却没有切。后果是**每一段讲解都渲染成一整段**：模型按讲法分好的段落、
 * 审查器量过的「平均段落字数」，到了屏幕上全没了。2026-10-07 UI 走查截图时才看出来，
 * 因为 16 个样例的判据只数标记有没有被吃掉，不数段落。
 *
 * 列表与引用仍然跨空行合并（松散列表 `- a\n\n- b` 是同一张列表）。
 */
function blocks(text: string): Block[] {
  const out: Block[] = [];
  let paragraphOpen = false;
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      paragraphOpen = false;
      continue;
    }

    const ul = /^[-*]\s+(.*)$/.exec(line);
    const ol = /^\d+\.\s+(.*)$/.exec(line);
    const quote = /^>\s?(.*)$/.exec(line);
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    const last = out[out.length - 1];

    if (ul) {
      if (last?.kind === "ul") last.items.push(ul[1]);
      else out.push({ kind: "ul", items: [ul[1]] });
    } else if (ol) {
      if (last?.kind === "ol") last.items.push(ol[1]);
      else out.push({ kind: "ol", items: [ol[1]] });
    } else if (quote) {
      if (last?.kind === "quote") last.lines.push(quote[1]);
      else out.push({ kind: "quote", lines: [quote[1]] });
    } else if (heading) {
      out.push({ kind: "h", lines: [heading[1]] });
    } else if (last?.kind === "p" && paragraphOpen) {
      last.lines.push(line);
    } else {
      out.push({ kind: "p", lines: [line] });
    }
    paragraphOpen = !ul && !ol && !quote && !heading;
  }
  return out;
}

/** 两侧都是中文（含全角标点）时直接接上，否则补一个空格——中文行间多出的空格会显示成一道缝。 */
const CJK = /[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]/;
export function joinLines(lines: string[]): string {
  return lines.reduce((acc, line) =>
    acc === "" ? line : CJK.test(acc.at(-1)!) || CJK.test(line[0]) ? acc + line : `${acc} ${line}`, "");
}

export function Markdown({ text }: { text: string }) {
  return (
    <>
      {blocks(text).map((block, i) => {
        const key = `b${i}`;
        switch (block.kind) {
          case "h":
            return (
              <p key={key} className="md-h mt-4 font-[var(--fw-semibold)] first:mt-0">
                {inline(block.lines[0], key)}
              </p>
            );
          case "quote":
            return (
              <blockquote
                key={key}
                className="my-3 border-l-2 border-[var(--c-accent)] bg-[var(--c-quote-bg)] py-2 pl-3 pr-2 text-[0.95em] text-[var(--c-ink-soft)]"
              >
                {inline(joinLines(block.lines), key)}
              </blockquote>
            );
          case "ul":
            return (
              <ul key={key} className="my-3 list-disc space-y-1.5 pl-5">
                {block.items.map((item, j) => (
                  <li key={`${key}-${j}`}>{inline(item, `${key}-${j}`)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key} className="my-3 list-decimal space-y-1.5 pl-5">
                {block.items.map((item, j) => (
                  <li key={`${key}-${j}`}>{inline(item, `${key}-${j}`)}</li>
                ))}
              </ol>
            );
          default:
            return (
              <p key={key} className="my-3 first:mt-0 last:mb-0">
                {inline(joinLines(block.lines), key)}
              </p>
            );
        }
      })}
    </>
  );
}
