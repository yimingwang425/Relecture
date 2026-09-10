/**
 * 每一步的停留时长。**只采集，不判读**（口径 F38 / 任务书 §3.7）。
 *
 * ## 算法定死在这里，不在页面里各写一份
 *
 * 从该步**渲染完成**到**点选**，取 `performance.now()` 的差值。
 * 用 `performance.now()` 而不是 `Date.now()`：后者会被系统对时、夏令时、
 * NTP 校正跳变影响，而那些跳变**不会报错**，只会让某几条记录出现负数或几小时的时长。
 *
 * ## 切走标签页不扣除，但同时记 `hidden_ms`
 *
 * `dwell_ms` 是墙钟意义上的「这一步花了多久」，页面不可见的时间**照算**；
 * `hidden_ms` 另记一份「其中有多久页面是不可见的」。
 *
 * 只记一个总数的话，「读了 40 秒」和「挂着 40 秒」永久混在一起，事后分不开——
 * 而这两者对「用户是不是在认真比较」的含义完全相反。两个数都记下来，
 * 想怎么组合是分析时的事；只记一个，另一个永远补不回来。
 *
 * ## 这里没有任何毫秒阈值常量，也不许有（口径 F38 / F29）
 *
 * v1 **不设阈值、不做任何「乱点」判定**。我们没有任何先验知道读完一段 400 字讲解
 * 要多久，现在拍一个数字是瞎猜。判读标准等内测第 1 周数据落盘后单独立一条口径，
 * 且立的时候要写明这条线是怎么来的——F29 的直接应用：观察项的判读标准跑之前写死，
 * 事后不得按结果重划分类。**拿到数据再按分布划线，等于用结果定义判据。**
 *
 * ## 为什么可见性由调用方喂进来
 *
 * `markHidden()` / `markVisible()` 是显式方法，而不是在这里挂 `visibilitychange`：
 * 挂监听要清理，清理漏了就跨步累加；更要紧的是**挂了监听就没法在断言里跑它**
 * （Node 里没有 document）。调用方在 `useEffect` 里接线，本模块保持纯计算。
 */

export type DwellReading = {
  /** 该步渲染完成 → 点选，毫秒。页面不可见的时间**照算**。 */
  dwell_ms: number;
  /** 其中页面不可见的累计毫秒。`hidden_ms <= dwell_ms` 恒成立。 */
  hidden_ms: number;
};

export type DwellClock = {
  /** 进入新一步时调用：计时归零，隐藏累计清空。 */
  reset(): void;
  /** 页面转为不可见。重复调用无副作用（已在隐藏中就忽略）。 */
  markHidden(): void;
  /** 页面转为可见。重复调用无副作用。 */
  markVisible(): void;
  /** 读数。**不停表**——读完还能继续走，因为同一步会先发 shown 再发 chosen。 */
  read(): DwellReading;
};

/**
 * `now` 可注入，好让断言喂一个可控的时间序列。默认用 `performance.now()`；
 * 拿不到（SSR、老环境）时退到 `Date.now()`——**退化要有，但不静默**：
 * 退化时两种时钟的语义不同（后者可能跳变），所以这里只在真的没有 performance 时才退。
 */
export function createDwellClock(
  now: () => number = () =>
    typeof performance !== "undefined" ? performance.now() : Date.now(),
): DwellClock {
  let startedAt = now();
  let hiddenTotal = 0;
  let hiddenSince: number | null = null;

  return {
    reset() {
      startedAt = now();
      hiddenTotal = 0;
      // 在隐藏状态下换步（用户切回来之前页面自己推进了一步）时，
      // 新一步的隐藏计时要从现在起算，而不是丢掉「现在仍然是隐藏的」这个事实。
      hiddenSince = hiddenSince === null ? null : now();
    },
    markHidden() {
      if (hiddenSince === null) hiddenSince = now();
    },
    markVisible() {
      if (hiddenSince !== null) {
        hiddenTotal += now() - hiddenSince;
        hiddenSince = null;
      }
    },
    read() {
      const t = now();
      const hidden = hiddenTotal + (hiddenSince === null ? 0 : t - hiddenSince);
      // 四舍五入到毫秒：performance.now() 给的是亚毫秒浮点，
      // 原样存进 jsonb 会让每条记录多一串没有意义的小数位。
      return { dwell_ms: Math.round(t - startedAt), hidden_ms: Math.round(hidden) };
    },
  };
}
