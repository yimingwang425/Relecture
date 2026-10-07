import type { Metadata, Viewport } from "next";
import { Figtree, Instrument_Serif, Noto_Serif_SC } from "next/font/google";

import "./globals.css";

/**
 * 三套字，各管一件事（2026-10-07 UI 改版，设计说明见 globals.css 文件头）：
 *
 * - **Figtree**：界面文字（按钮、标签、说明）。拉丁字形；中文由系统黑体接管。
 * - **Instrument Serif**：文字标志与大号拉丁标题，主要用它的斜体。
 * - **Noto Serif SC**：中文标题与**讲解正文**。这是一个读东西的地方，正文用宋体。
 *
 * 全部走 `next/font`（构建期下载、自托管，运行期没有第三方请求——出境清单
 * PRD §13.3 不必多一项）。⚠️ 代价登记：**构建期需要网络**，离线构建会显式失败。
 *
 * Noto Serif SC 的 `preload: false`：中文字体按 unicode-range 切成上百片，
 * 浏览器只下载页面上真的出现的字所在的那几片；预加载反而会抢首屏带宽。
 * next/font 的子集表里没有中文（它只列拉丁 / 西里尔），所以 `subsets` 填 latin 只是为了过校验，
 * 中文切片照样全部自托管。
 */
const figtree = Figtree({
  subsets: ["latin"],
  variable: "--font-figtree",
  display: "swap",
});

const instrument = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  style: ["normal", "italic"],
  variable: "--font-instrument",
  display: "swap",
});

const notoSerif = Noto_Serif_SC({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-noto-serif",
  display: "swap",
  preload: false,
});

export const metadata: Metadata = {
  title: { default: "Relecture", template: "%s · Relecture" },
  description: "选择适合你的讲解方式，在自己的节奏里弄懂每个问题。",
};

export const viewport: Viewport = {
  themeColor: "#f3efe6",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${figtree.variable} ${instrument.variable} ${notoSerif.variable}`}>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
