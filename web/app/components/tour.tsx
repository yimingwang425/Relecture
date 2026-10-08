"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { ADJUSTMENTS } from "@/lib/style/adjust";

/**
 * 上手引导（2026-09-22 起；**2026-10-08 重画**）。
 *
 * ## 三步讲什么（2026-10-08 按现状重排）
 *
 * 原来三步全在讲「调整讲法」。那之后产品变了三处，引导一处都没跟上：
 *
 * 1. **问** —— 讲解 agent 会自己翻课件、不够就再找，回答下面标出处页（口径 F109–F111）。
 *    第一次用的人最该知道的是「它讲的东西有出处、可以对页」。
 * 2. **追问** —— 难点交给追问（口径 F107：「卡住的时候」「这部分没看懂」撤掉了）。
 *    没听懂就接着问，它会换个角度讲那一点。
 * 3. **调整讲法** —— 相对调整 + 按新讲法重讲上一个问题（口径 F107）。
 *
 * ## 动图为什么重画
 *
 * 原来的假界面用 5px 字画了一张缩略图，再放大 2–3 倍去「对准」一个角落——
 * **大半时间镜头对着空白**（2026-10-08 用户反馈：「显示大约十分之一，其余是白的」），
 * 而 5px 的字放大之后仍然是糊的色块，不像真界面。
 *
 * 现在：
 * - **按真尺寸画**：画布固定设计像素（宽屏 520、窄屏 400，高 360），里面用的是页面上**真的那几个类**
 *   （`.bubble` / `.composer__box` / `.choice` / `.chip` / `.notice` / `.btn`），字号与真界面相同；
 *   整块再按弹窗宽度**等比缩小**到刚好铺满画框——不放大、不裁切，没有留白。
 * - **按钮文字取自 `ADJUSTMENTS`**，不抄一份：改了那张表，引导自己跟上。
 * - **不用 CSS 关键帧排时间线**：按阶段推进（`SCENES[k].phases`），每一阶段让几样东西出现，
 *   鼠标走到 `data-cur` 标着的那个元素上点一下——落点从真实布局里量，布局变了不会点歪。
 * - `prefers-reduced-motion` 时不播，直接停在最后一帧。
 *
 * 代价仍如实登记：它是**示意**，不是截图——侧栏、页签这些与这一步无关的东西没画。
 *
 * ## 只弹一次，记在这台设备上
 *
 * localStorage 可能丢（隐私模式、清数据），丢了就再弹一次，代价是一次多余的引导。
 * 读它用 `useSyncExternalStore` 而不是在 effect 里 setState（级联渲染）。
 * 键名带 `v2`：三步的内容换了，看过旧版的人也该看一次新的。
 */

const SEEN_KEY = "relecture.tour.seen.v2";

/** 画布的设计尺寸（px）。真界面的字号在这个尺寸下画，再整体等比缩放。
 *  窄屏换一张更窄的画布（内容照常折行），否则手机上整体缩到 0.6 倍、字小得读不了。 */
const CANVAS_WIDE = 520;
const CANVAS_NARROW = 400;
const CANVAS_H = 360;

type Phase = {
  /** 这一阶段停多久（ms）。 */
  ms: number;
  /** 鼠标停在哪个 `data-cur` 上；不给就不显示鼠标。 */
  cursor?: string;
  /** 这一阶段结束前按一下。 */
  click?: boolean;
};

type Scene = {
  title: string;
  lead: string;
  phases: Phase[];
  render: (
    p: number,
    typed: (text: string, from: number, to: number) => string,
  ) => React.ReactNode;
};

// ---------------------------------------------------------------- 画面里的几样共用件

function Composer({
  text,
  placeholder,
  adjustTarget,
}: {
  text: string;
  placeholder: string;
  adjustTarget?: boolean;
}) {
  return (
    <div className="composer__box tk-composer">
      <div className={`tk-input${text ? "" : " tk-input--empty"}`}>
        {text || placeholder}
        {text && <span className="tk-caret" aria-hidden />}
      </div>
      <div className="composer__foot">
        <span className="t-caption">适中</span>
        <span className="flex items-center gap-2">
          <span
            className={`btn btn--ghost btn--sm${adjustTarget ? " tk-target" : ""}`}
            data-cur="adjust"
          >
            调整讲法
          </span>
          <span
            className={`btn btn--primary btn--sm${text ? "" : " tk-dim"}`}
            data-cur="send"
          >
            发送
          </span>
        </span>
      </div>
    </div>
  );
}

function Status({ text }: { text: string }) {
  return (
    <p className="t-caption tk-status">
      <span aria-hidden className="spinner" />
      {text}
    </p>
  );
}

/** 画面顶上那一条：课程名。每一幕都有，让人一眼认出「这是提问页」。 */
function Header() {
  return (
    <div className="tk-head">
      <b>22.081J 能源导论</b>
      <span className="t-caption">2 份课件</span>
    </div>
  );
}

/** 上一轮：问 + 答。第二、三幕从这里接着往下。 */
function PastTurn({ faded = false }: { faded?: boolean }) {
  return (
    <div className={`tk-past${faded ? " tk-faded" : ""}`}>
      <div className="bubble tk-bubble">{Q1}</div>
      <div className="tk-answer">
        <p>热机只能把一部分热变成功，效率上限是 1 − T冷 / T热（第 20 页）。</p>
        <p>剩下的热必须排到低温那一端，这就是电厂的废热。</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 三幕

const Q1 = "为什么发电厂不能把热全部变成电？";
const Q2 = "第二句没太懂，能举个例子吗？";

const SCENES: Scene[] = [
  {
    title: "问课件里的任何问题",
    lead: "它会先翻你的课件，觉得不够就自己再去找。每段讲解下面都标着出处页码，点开能看原文片段。",
    phases: [
      { ms: 700 },
      { ms: 1500 }, // 1 打字
      { ms: 700, cursor: "send", click: true }, // 2
      { ms: 1100 }, // 3 检索
      { ms: 1400 }, // 4 agent 再找
      { ms: 1500 }, // 5 正文
      { ms: 2600, cursor: "sources" }, // 6 出处
    ],
    render: (p, typed) => (
      <div className="tk-chat">
        <Header />
        <div className="tk-thread">
          {p < 3 && <p className="tk-empty">还没有问过问题。</p>}
          {p >= 3 && <div className="bubble tk-bubble tk-enter">{Q1}</div>}
          {p === 3 && <Status text="正在检索课件 · 已等 1 秒" />}
          {p === 4 && <Status text="正在课件里再找一找 · 已等 4 秒" />}
          {p >= 5 && (
            <div className="tk-answer tk-enter">
              <p>
                热机只能把一部分热变成功，效率上限是 <b>1 − T冷 / T热</b>（第 20
                页）。
              </p>
              <p>剩下的热必须排到低温那一端，这就是电厂的废热。</p>
              {p >= 6 && (
                <div className="tk-sources tk-enter">
                  <span className="t-caption">出处</span>
                  <span className="chip tk-target" data-cur="sources">
                    lec01b.pdf{" "}
                    <span className="text-[var(--c-muted-text)]">
                      第 20、21 页
                    </span>
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
        <Composer
          text={p >= 3 ? "" : typed(Q1, 1, 2)}
          placeholder="问这门课的任何问题"
        />
      </div>
    ),
  },
  {
    title: "没听懂？接着问",
    lead: "「这一步没懂」「能举个例子吗」——它记得上一轮讲了什么，会换个角度、用更浅白的说法把那一点再讲一遍。",
    phases: [
      { ms: 600 },
      { ms: 1500 }, // 1 打字
      { ms: 700, cursor: "send", click: true }, // 2
      { ms: 1200 }, // 3 组织讲解
      { ms: 3200 }, // 4 新的讲解
    ],
    render: (p, typed) => (
      <div className="tk-chat">
        <Header />
        <div className="tk-thread">
          <PastTurn faded={p >= 3} />
          {p >= 3 && <div className="bubble tk-bubble tk-enter">{Q2}</div>}
          {p === 3 && <Status text="正在组织讲解 · 已等 2 秒" />}
          {p >= 4 && (
            <div className="tk-answer tk-enter">
              <p>
                打个比方：水车要转，水得从高处流到低处。没有「低处」接着，水车就转不起来。
              </p>
              <p>
                热也一样，得有个更冷的地方接住剩下的热，所以总有一部分要排掉。
              </p>
            </div>
          )}
        </div>
        <Composer
          text={p >= 3 ? "" : typed(Q2, 1, 2)}
          placeholder="接着问，或者问个新问题"
        />
      </div>
    ),
  },
  {
    title: "讲法不合适？点「调整讲法」",
    lead: "和刚才比想怎么变就点哪个，每个只改一样；也可以直接写一句。改完可以让它按新讲法把上一个问题重讲一遍。",
    phases: [
      { ms: 700 },
      { ms: 900, cursor: "adjust", click: true }, // 1
      { ms: 900, cursor: "adj-shorter", click: true }, // 2 面板 → 点「再短一点」
      { ms: 900, cursor: "adj-more_examples", click: true }, // 3 点「多举例子」
      { ms: 800, cursor: "apply", click: true }, // 4 应用
      { ms: 2800, cursor: "redo" }, // 5 改好了 + 重讲
    ],
    render: (p) => {
      const picked = new Set<string>([
        ...(p >= 3 ? ["shorter"] : []),
        ...(p >= 4 ? ["more_examples"] : []),
      ]);
      return (
        <div className="tk-chat">
          <Header />
          <div className="tk-thread">
            <PastTurn />
          </div>
          <Composer
            text=""
            placeholder="问这门课的任何问题"
            adjustTarget={p === 1}
          />

          {/* 真界面是原生 <dialog>，背后整页压暗（`.dialog::backdrop`）。这里照画一层。 */}
          {p >= 2 && <div className="tk-backdrop tk-enter" />}
          {p >= 2 && (
            <div className="tk-panel tk-enter">
              <div className="tk-panel__head">
                <b>调整讲法</b>
                <span className="btn btn--ghost btn--sm">关闭</span>
              </div>
              <div className="tk-panel__body">
                <p className="field-label">和刚才比</p>
                <div className="tk-grid">
                  {ADJUSTMENTS.map((a) => (
                    <span
                      key={a.id}
                      className="choice tk-choice"
                      // 应用之后真界面会清空已选（`setPicked([])`），这里照做。
                      data-selected={p < 5 && picked.has(a.id)}
                      data-cur={`adj-${a.id}`}
                    >
                      {a.label}
                    </span>
                  ))}
                </div>
                {p < 5 ? (
                  <span
                    className="btn btn--primary btn--sm mt-3"
                    data-cur="apply"
                  >
                    应用
                  </span>
                ) : (
                  <div className="notice notice--info tk-done tk-enter">
                    <span>已经改好了，之后的讲解按新的讲法。</span>
                    <span
                      className="btn btn--primary btn--sm tk-target"
                      data-cur="redo"
                    >
                      按新讲法重讲上一个问题
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      );
    },
  },
];

// ---------------------------------------------------------------- 时间线

function useReducedMotion(): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia?.("(prefers-reduced-motion: reduce)");
      m?.addEventListener?.("change", cb);
      return () => m?.removeEventListener?.("change", cb);
    },
    () =>
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    () => false,
  );
}

/** 第几阶段 + 这一阶段已经过去的比例（打字用）。减弱动效时停在最后一帧。 */
function useTimeline(
  phases: Phase[],
  reduced: boolean,
): { p: number; frac: number } {
  const [state, setState] = useState({ p: 0, frac: 0 });
  useEffect(() => {
    if (reduced) return;
    let p = 0;
    let started = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      let el = now - started;
      while (el >= phases[p].ms) {
        el -= phases[p].ms;
        started += phases[p].ms;
        p = (p + 1) % phases.length;
      }
      setState({ p, frac: el / phases[p].ms });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [phases, reduced]);
  return reduced ? { p: phases.length - 1, frac: 1 } : state;
}

function Stage({ scene }: { scene: Scene }) {
  const reduced = useReducedMotion();
  const { p, frac } = useTimeline(scene.phases, reduced);
  const [box, setBox] = useState({ w: CANVAS_WIDE, k: 1 });
  const [cur, setCur] = useState<{ x: number; y: number } | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const roRef = useRef<ResizeObserver | null>(null);

  // 等比缩放：画布永远刚好铺满画框的宽度。
  const stageRef = useCallback((el: HTMLDivElement | null) => {
    roRef.current?.disconnect();
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const sw = e.contentRect.width;
      const w = sw < 440 ? CANVAS_NARROW : CANVAS_WIDE;
      // 高度也要装得下：弹窗最高 80dvh（`.dialog`），标题、说明与按钮约占 300px。
      // 只按宽度缩放的话，矮屏（768px 的笔记本）上按钮会被挤进弹窗的滚动区里，看不见。
      const maxH = Math.max(200, window.innerHeight * 0.8 - 300);
      const k = Math.min(sw / w, maxH / CANVAS_H);
      setBox({ w, k });
    });
    ro.observe(el);
    roRef.current = ro;
  }, []);

  /** 打字：第 `from` 阶段从零打起，到第 `to` 阶段开始时打完。 */
  const typed = (text: string, from: number, to: number) => {
    if (p < from) return "";
    if (p >= to) return text;
    return text.slice(0, Math.ceil(text.length * frac));
  };

  // 鼠标落点**从真实布局里量**：画面里的元素换了位置，鼠标跟着换，不会点歪。
  const target = scene.phases[p].cursor;
  useEffect(() => {
    const canvas = canvasRef.current;
    const el = target
      ? canvas?.querySelector<HTMLElement>(`[data-cur="${target}"]`)
      : null;
    if (!canvas || !el) return setCur(null);
    const c = canvas.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const scale = c.width / box.w || 1;
    setCur({
      x: (r.left - c.left + r.width * 0.55) / scale,
      y: (r.top - c.top + r.height * 0.6) / scale,
    });
  }, [target, p, box]);

  const clicking = scene.phases[p].click && frac > 0.55 && frac < 0.8;

  return (
    // 外层量宽度，里层画框跟着画面一起缩：矮屏上按高度缩小时两侧不留空边。
    <div ref={stageRef}>
      <div
        className="tour-stage"
        style={{ width: box.w * box.k, height: CANVAS_H * box.k }}
      >
        <div
          ref={canvasRef}
          className="tk"
          style={{
            width: box.w,
            height: CANVAS_H,
            transform: `scale(${box.k})`,
          }}
          aria-hidden
        >
          {scene.render(p, typed)}
          {cur && !reduced && (
            <span
              className={`tk-cursor${clicking ? " tk-cursor--down" : ""}`}
              style={{ transform: `translate(${cur.x}px, ${cur.y}px)` }}
            >
              <svg width="18" height="22" viewBox="0 0 18 22" aria-hidden>
                <path
                  d="M2 1.5v16.2l4.3-4.1 2.9 6.6 2.7-1.2-2.9-6.5h6z"
                  fill="#111"
                  stroke="#fff"
                  strokeWidth="1.4"
                />
              </svg>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 弹窗

export function Tour() {
  const stored = useSyncExternalStore(
    () => () => {},
    () => {
      try {
        return window.localStorage.getItem(SEEN_KEY) === "1";
      } catch {
        return false;
      }
    },
    () => true, // 服务端当作看过：SSR 不渲染，hydrate 时再按真实值决定
  );
  const [closed, setClosed] = useState(false);
  const [i, setI] = useState(0);
  const dialogRef = useRef<HTMLDialogElement>(null);

  function finish() {
    setClosed(true);
    dialogRef.current?.close();
    try {
      window.localStorage.setItem(SEEN_KEY, "1");
    } catch {
      // 存不进去只意味着下次还会弹一次。
    }
  }

  if (stored || closed) return null;

  const step = SCENES[i];
  const last = i === SCENES.length - 1;

  return (
    <dialog
      ref={(el) => {
        dialogRef.current = el;
        // 挂上就打开。`showModal()` 只能在元素进 DOM 之后调。
        if (el && !el.open) el.showModal();
      }}
      className="dialog tour-dialog"
      data-field="tour"
      data-step={i + 1}
      onCancel={(e) => {
        // Esc 关掉也算看过——否则每次进页面都再弹一次。
        e.preventDefault();
        finish();
      }}
    >
      <div className="dialog__body">
        {/* key：换一步就重挂载，时间线从头播。 */}
        <Stage key={i} scene={step} />

        <p className="t-caption mt-5">
          {i + 1} / {SCENES.length}
        </p>
        <h2 className="t-title mt-1 text-[17px]">{step.title}</h2>
        <p className="t-body mt-2">{step.lead}</p>

        <div className="mt-6 flex items-center justify-between gap-4">
          <div className="tour-dots" data-field="tour-dots">
            {SCENES.map((s, n) => (
              <button
                key={s.title}
                type="button"
                className="tour-dot"
                aria-current={n === i}
                aria-label={`第 ${n + 1} 步：${s.title}`}
                onClick={() => setI(n)}
              />
            ))}
          </div>

          <div className="flex gap-2">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              data-field="tour-prev"
              disabled={i === 0}
              onClick={() => setI((v) => Math.max(0, v - 1))}
            >
              上一步
            </button>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              data-field="tour-next"
              onClick={() => (last ? finish() : setI((v) => v + 1))}
            >
              {last ? "开始提问" : "下一步"}
            </button>
          </div>
        </div>
      </div>
    </dialog>
  );
}
