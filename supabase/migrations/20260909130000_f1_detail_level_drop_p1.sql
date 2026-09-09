-- F1 阶段 3 · 详细程度 CHECK 收紧到 P0/P2/P3（P1 退出）
-- 日期：2026-09-09　口径见 f1_samples/README.md 的 F23（产品档位 ≠ 扫描点位）
--
-- 为什么现在做：阶段 1 建表时四档都在，P1 是在阶段 2 被实测推翻的
-- （`detail_p0_p1` 连续 8 次生成隶属集合恒相同，第 4 次 P1 甚至比 P0 更长）。
-- 当时没有写入路径会产生 P1，留着无害；阶段 3 开始 onboarding 与设置页会写这一列，
-- 于是 CHECK 必须与 `f1_samples/style_axes.py` 的 AXES["detail_level"] 对齐——
-- 两处取值域不一致时，前端拒绝的取值数据库仍然接受，反过来也一样。
--
-- **本文件是 style_config 的 CHECK 的最新出处**：
-- `20260907120000_f1_style_config.sql` 里那条 CHECK 仍写着四档，那是历史，不要回改
-- （不修改已有迁移，任务书 §五）。护栏 `web/scripts/verify_style_axes.mjs` 按时间戳顺序
-- 取每条约束的**最后一次定义**，所以它读到的是本文件。

-- ---------------------------------------------------------------- 存量检查

-- 口径 F10 的同一条原则：**不静默改写用户配置**。
--
-- P1 → P0 还是 P1 → P2 是产品决策（P1(40–90) 被 P0(30–120) 包含，但区间中位更靠近 P2），
-- 不该由一句顺手的 update 替人做掉。预期行数是 0：F09 的触发器建行时 detail_level
-- 取默认值 P0，而阶段 3 之前没有任何写入路径会产生 P1。
--
-- 若真有 P1 行，这里**抛异常让整条迁移回滚**，而不是跳过或改写：
-- 迁移是事务性的，中止是安全的；带着一批被静默改写的用户配置继续跑不是。
do $$
declare
  n int;
begin
  select count(*) into n from public.style_config where detail_level = 'P1';
  if n > 0 then
    raise exception
      '存量有 % 行 detail_level = P1，本迁移拒绝静默改写。'
      '先决定这批用户移到 P0 还是 P2（是产品决策，见 f1_samples/README.md §三 发现 B），'
      '在单独一条迁移里显式写明并执行，再跑本迁移。', n;
  end if;
  raise notice '[F1 阶段 3] P1 存量行数：%  (0 = 预期)', n;
end
$$;

-- ---------------------------------------------------------------- 执行记录
--
-- ⚠️ **这段守卫大概率永远不触发**：没有任何写入路径会产生 P1（阶段 3 之前没有写入路径，
-- 之后前端只写取值域内的值）。**一条永远不触发的守卫，和一条被删掉的守卫，表现完全一样**
-- ——通则七 #5 的形态。所以它的有效性不能靠「跑了没报错」来说明。
--
-- 两处支撑，分开写，别混为一谈：
--
-- 1. **机制已在本地验过两面**（口径 F12：破坏性验证只在本地）。
--    `verify_rls_local.mjs` 的「P1 收紧 · 迁移守卫的两面」：
--    造一行 P1 → 本迁移抛异常且那一行**未被改写**；无 P1 → 正常通过，
--    且此后写 P1 被 CHECK 拒（23514）而写 P2 成功。
--
-- 2. **生产存量：至今没有观测到。**
--
--        生产执行日期：2026-09-10　执行结果：成功，未回滚，CHECK 已加上
--        notice 行数：**未取到** —— Supabase SQL Editor 不显示 NOTICE
--
--    执行后单独查了一次分布：
--
--        select detail_level, count(*) from public.style_config group by 1;
--        → **返回 0 行**
--
--    **表是空的**，所以这次 CHECK 是**空转通过**：没有 P1，是因为没有任何行，
--    不是因为「存量里恰好没有 P1」。故「生产无 P1」在此刻**不携带信息**——
--    有真实用户之后需要重查（口径 F34）。
--
--    ⚠️ 别把「迁移成功」读成「存量安全」：这次迁移只证明了 DDL 能加上，
--    没有证明守卫在有数据时会拦。守卫拦不拦，靠的是上面第 1 条的本地两面验证。

-- ---------------------------------------------------------------- 收紧 CHECK

alter table public.style_config
  drop constraint if exists style_config_detail_level_check;

alter table public.style_config
  add constraint style_config_detail_level_check
    check (detail_level in ('P0', 'P2', 'P3'));

-- ---------------------------------------------------------------- 反馈映射的出处

-- 口径 F05：「太啰嗦」沿详细程度轴下移一格。P1 退出后这条阶梯是 P0 → P2 → P3，
-- **不再经过 P1**。实现在 web/lib/style/feedback.ts；这里只把顺序钉进注释，
-- 因为顺序本身就是 CHECK 里那三个取值的顺序，两处漂了 verify_style_axes.mjs 会红。
--
-- 刻意不建 f1_detail_ladder() 这类数据库函数：阶段 3 没有任何数据库侧的消费方，
-- 建一个没人调的函数等于第二份出处（约束 3 / F13 的同一个坑）。
comment on column public.style_config.detail_level is
  'A 类档位，被 a_rules 的 paragraph_length 审查执行。'
  '取值域 P0/P2/P3——P1 已退出（口径 F23，2026-09-09）。'
  '反馈「太啰嗦」沿 P0→P2→P3 下移一格（口径 F05），实现见 web/lib/style/feedback.ts。';
