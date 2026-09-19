/**
 * 读 `f1_samples/pairs.json`，投影成页面要的最小形状。
 *
 * ## 为什么是 import 而不是 fs.readFile
 *
 * `pairs.json` 在仓库根的 `f1_samples/` 下，**不在 Vercel 的 Root Directory（`web`）里**。
 * 运行期 `fs.readFile("../../f1_samples/pairs.json")` 在 serverless 上找不到文件；
 * import 则在**构建期**被打进产物，文件缺失时 `next build` 直接失败——
 * 这正是想要的形态：缺样例是坏状态，应该在构建时炸，而不是让用户打开一个空页面。
 *
 * 复制一份进 `web/` 是另一条路，**没走**：那就有了第二份样例资产，
 * 而 `explain` 运行时读的是根目录那一份，两份迟早漂（约束 3 的同一个坑）。
 *
 * ## 为什么要投影
 *
 * `pairs.json` 每个样例还带着 `prompt`（含 system 全文的 sha、A/B 类规则原文）、
 * `style_config`、`config_fingerprint` 等生产元数据。那些是核对用的，
 * **不该进浏览器 bundle**——既没用，又把生产 prompt 的形状暴露给页面。
 * 这里只取 id / 档位 / 正文三样。
 */

import rawPairs from "../../../f1_samples/pairs.json";
import { REACHABLE_PAIR_IDS } from "./flow";

export type SampleView = {
  sampleId: string;
  /** 该样例在目标轴上的取值，如 `P0` / `more` / `what`。 */
  variantLabel: string;
  text: string;
};

export type PairView = {
  pairId: string;
  targetDimension: string;
  /** 这一对在回答的问题。**每对各不相同**（2026-09-19）：8 对共用一个问题时，
   *  用户连读 12 段几乎一样的内容，「选了跟没选没区别」。 */
  question: string;
  samples: [SampleView, SampleView];
};

type RawSample = { sample_id: string; variant_label: string; text: string };
type RawPair = { pair_id: string; target_dimension: string; question: string; samples: RawSample[] };
type RawFile = { pairs: RawPair[] };

const file = rawPairs as unknown as RawFile;

// 顶层不再有单一问题（每对一个，见 `PairView.question`）。

function toView(pair: RawPair): PairView {
  if (pair.samples.length !== 2) {
    throw new Error(`${pair.pair_id} 有 ${pair.samples.length} 个样例，应为 2 个`);
  }
  const [a, b] = pair.samples;
  const view = (s: RawSample): SampleView => ({
    sampleId: s.sample_id,
    variantLabel: s.variant_label,
    text: s.text,
  });
  if (!pair.question) {
    // 缺问题就抛：页面会写「两段回答的是：…」，缺了那句话会变成空白，
    // 而用户不知道这两段在回答什么就没法比讲法。
    throw new Error(`${pair.pair_id} 没有 question —— pairs.json 是旧版？重生成样例`);
  }
  return {
    pairId: pair.pair_id,
    targetDimension: pair.target_dimension,
    question: pair.question,
    samples: [view(a), view(b)],
  };
}

/**
 * 比较树可能走到的全部对。**缺任何一对就抛异常**，不返回一个少几对的字典——
 * 少一对的表现是「用户走到第 2 步页面空白」，而那时候已经没人知道是数据缺了。
 */
export function loadPairs(): Record<string, PairView> {
  const byId = new Map(file.pairs.map((p) => [p.pair_id, p]));
  const out: Record<string, PairView> = {};
  const missing: string[] = [];

  for (const id of REACHABLE_PAIR_IDS) {
    const pair = byId.get(id);
    if (!pair) missing.push(id);
    else out[id] = toView(pair);
  }

  if (missing.length > 0) {
    throw new Error(
      `f1_samples/pairs.json 缺少比较树要用的对：${missing.join(", ")}。` +
        `重生成样例后 pair_id 变了？改 flow.ts 的树，不要在这里兜底。`,
    );
  }
  return out;
}
