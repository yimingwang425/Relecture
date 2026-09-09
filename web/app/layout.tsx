import type { Metadata } from "next";
import { Figtree } from "next/font/google";

import "./globals.css";

/**
 * 参考稿（docs/F1/UI设计.md）指定 Figtree 可变字体，字重轴 100–900。
 *
 * 走 `next/font` 而不是 `<link>` 到 Google Fonts：next/font 在**构建期**下载并自托管，
 * 运行期没有第三方请求——出境清单（PRD §13.3）里就不必多一个 fonts.gstatic.com，
 * 而那份清单是隐私政策的附件，能少一项是一项。
 *
 * ⚠️ 代价登记：**构建期需要网络**。离线构建会失败（且是显式报错，不是静默降级）。
 * Figtree 不含中文字形，中文由 globals.css 的回退字体栈接管。
 */
const figtree = Figtree({
  subsets: ["latin"],
  variable: "--font-figtree",
  display: "block",
});

export const metadata: Metadata = {
  title: "AI 课件讲解助手",
  description: "F1 风格获取",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={figtree.variable}>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
