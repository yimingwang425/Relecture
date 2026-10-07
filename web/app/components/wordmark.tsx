/**
 * 文字标志：「Relecture.」——Instrument Serif 斜体，句点是朱红的。
 *
 * 2026-10-07 替掉原来那个青色对话气泡（它的尺寸与颜色是照 Quantum² 参考稿配的）。
 * 新的标志就是名字本身：Relecture 是法语的「重读」，朱红句点是批注那支笔落下的第一下。
 * 纯文字，不是图片：跟着字体与颜色变量走，改主题时不会有一张图过期。
 */
export function Wordmark() {
  return (
    <>
      Relecture<span className="wordmark-dot">.</span>
    </>
  );
}
