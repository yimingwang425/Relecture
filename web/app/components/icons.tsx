/**
 * 界面里用到的几个线性图标。手写 SVG、`currentColor` 描边，跟着文字颜色走。
 *
 * 不引图标库：一共就这几个，库进 bundle 换来的是几千个用不到的图形。
 * 原来侧栏用的是 emoji（📄 ＋），在不同系统上长得完全不一样，也不跟颜色变量走。
 */
type P = { size?: number; className?: string };

function Svg({ size = 18, className = "", children }: P & { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
    >
      {children}
    </svg>
  );
}

export const IconMenu = (p: P) => (
  <Svg {...p}>
    <path d="M4 7h16M4 12h10M4 17h16" />
  </Svg>
);

export const IconDoc = (p: P) => (
  <Svg {...p}>
    <path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z" />
    <path d="M14 3.5V8h4M10 12.5h5M10 16h5" />
  </Svg>
);

export const IconPlus = (p: P) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const IconPen = (p: P) => (
  <Svg {...p}>
    <path d="M14.5 5.5l4 4M5 19l1-4.5L15.8 4.7a1.4 1.4 0 0 1 2 0l1.5 1.5a1.4 1.4 0 0 1 0 2L9.5 18z" />
  </Svg>
);

export const IconClose = (p: P) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);

export const IconArrowUp = (p: P) => (
  <Svg {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Svg>
);

export const IconBack = (p: P) => (
  <Svg {...p}>
    <path d="M19 12H5M11 6l-6 6 6 6" />
  </Svg>
);
