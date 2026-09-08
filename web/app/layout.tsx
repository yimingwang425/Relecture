import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 课件讲解助手",
  description: "F1 风格获取 · 阶段 1.5 部署链",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-neutral-50 text-neutral-900 antialiased">
        {children}
      </body>
    </html>
  );
}
