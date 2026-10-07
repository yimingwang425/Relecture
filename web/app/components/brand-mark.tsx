/**
 * 本项目的标志：一个对话气泡里两行字。
 *
 * 参考稿的 logo 是 Quantum² 的品牌图形，不能拿来用；这里只沿用它的尺寸（26×25）
 * 与强调色 `#38c6ec`，让它落在黑色胶囊里的分量与参考稿一致。
 */
export function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg aria-hidden viewBox="0 0 26 26" width={size} height={size} className="shrink-0">
      <path
        d="M13 2C6.9 2 2 6.5 2 12.1c0 3 1.4 5.7 3.7 7.5L4.9 24l4.7-2.3c1.1.3 2.2.5 3.4.5 6.1 0 11-4.5 11-10.1S19.1 2 13 2Z"
        fill="#38c6ec"
      />
      <path d="M8.3 10.3h9.4M8.3 14.2h6" stroke="#000" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}
