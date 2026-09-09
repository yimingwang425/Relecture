import {
  AXIS_IDS,
  AXIS_LABEL,
  ENFORCED,
  VALUE_HINT,
  VALUE_LABEL,
  type AxisId,
  type StyleConfig,
} from "@/lib/style/axes";

/**
 * 四条轴的只读展示。两条臂的收尾屏与设置页共用同一个组件——
 * 三处各写一遍的话，「哪条轴被审查执行」这类说明迟早只剩一处是对的。
 *
 * `data-axis` / `data-value` 供回归工具定位，**不按中文标签匹配**：
 * 按文案匹配的断言在改文案时会静默变绿（阶段 1.5 已经踩过一次）。
 */
export function ConfigSummary({
  config,
  showEnforcedNote = true,
}: {
  config: StyleConfig;
  showEnforcedNote?: boolean;
}) {
  return (
    <>
      <dl className="card divide-y divide-[var(--c-border-row)] p-2">
        {AXIS_IDS.map((axis: AxisId) => (
          <div key={axis} data-axis={axis} className="flex items-baseline justify-between gap-4 p-4">
            <dt className="t-heading shrink-0">
              {AXIS_LABEL[axis]}
              {!ENFORCED[axis] && (
                <span className="t-meta ml-2 font-[var(--fw-regular)]">仅进 prompt</span>
              )}
            </dt>
            <dd className="text-right">
              <span data-value={config[axis]} className="t-ui">
                {(VALUE_LABEL[axis] as Record<string, string>)[config[axis]]}
              </span>
              <span className="t-meta mt-1 block max-w-[46ch]">
                {(VALUE_HINT[axis] as Record<string, string>)[config[axis]]}
              </span>
            </dd>
          </div>
        ))}
      </dl>

      {showEnforcedNote && (
        <p className="t-meta mt-3 max-w-[70ch] leading-relaxed">
          四条轴里只有<b>详细程度</b>会被自动审查：讲解生成后按它判定，不达标会重新生成。
          举例 / 结构 / 深度只写进给模型的要求里，<b>不做判定</b>（PRD §8.5 的判定尚未校准），
          所以它们更像倾向而不是硬约束。
        </p>
      )}
    </>
  );
}
