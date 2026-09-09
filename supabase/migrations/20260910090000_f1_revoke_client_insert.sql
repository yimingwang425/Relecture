-- F1 阶段 3 · 收回客户端的 insert 授权，并删掉 insert 策略
-- 日期：2026-09-10　触发条件见 f1_samples/README.md §二
--
-- 触发条件已满足：阶段 3 的三个页面**全部只走 update**——
-- onboarding 写四条轴 + onboarded_at，设置页写四条轴。客户端没有插入的场合。
--
-- 每个用户的行由 `handle_new_user_style_config` 触发器在注册时建好（口径 F09），
-- 所以这段授权在生产下永远走不到。留着不是「无害」而是「多一条没有生产场景的通路」。
--
-- ---------------------------------------------------------------- 两层，不是一层
--
-- 这里同时做两件事，它们挡的是不同的东西：
--
--   1. `revoke insert`  —— 权限层。authenticated 角色根本没有 insert 这个动作。
--   2. `drop policy`    —— 策略层。RLS 开着且**没有任何 insert 策略**时，
--                          即便有人把 grant 加回来，插入仍然被 RLS 拒。
--
-- 只做 1，某天有人 `grant insert` 就全开了；只做 2，权限还在，将来加错一条
-- 宽松策略就全开了。两层都做，任一层被误改都还有另一层。
--
-- ---------------------------------------------------------------- 触发器为什么不受影响
--
-- `handle_new_user_style_config` 是 **security definer**（见 20260907120000）：
-- 它以函数所有者的身份执行，不看调用者的权限。Supabase 注册时是
-- `supabase_auth_admin` 往 auth.users 插行，触发器随后以所有者身份写 style_config——
-- 收回 `authenticated` 的 insert 不碰这条路径。
--
-- **这句话不是推理，是被断言钉住的**（口径 F11：每条「拒绝」配一条「允许」）：
-- `verify_rls_local.mjs` 里有一条 `trigger_inserts_for_unprivileged_role`——
-- 用一个**对 style_config 没有任何权限**的角色往 auth.users 插行，
-- 要求 style_config 仍然多出一行；反向控制 R7 把 security definer 改成
-- security invoker，那条断言必须变红。少了这一对，本迁移可能在
-- **把注册一起打死**的情况下让「客户端插不进去」全绿。

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：2026-09-10　执行结果：成功，未回滚
--
-- **同一件事在这里也成立（口径 F34）**：执行时 `style_config` 是空表
-- （`select detail_level, count(*) ... group by 1` 返回 0 行），
-- 所以本迁移在生产上**只证明了 DDL 能执行**——收回授权确实生效、策略确实删掉了。
--
-- 它**没有**证明的两件事，都要等有真实用户之后才谈得上观测：
--
--   · 客户端 insert 会被拒（表里没有任何账号可以拿来试）；
--   · 注册仍能建行——也就是那条**允许控制**（人工清单第 11 条）。
--     ⚠️ 这条尤其要记住：**没有它，「客户端插不进去」在把注册一起打死的情况下也全绿**，
--     而那个坏状态要到第一个新用户发现自己没有配置行时才暴露（F09）。
--
-- 机制本身已在本地验过（口径 F12，破坏性验证只在本地）：
-- `verify_rls_local.mjs` 的 `trigger_inserts_for_unprivileged_role`（无权限角色注册仍建行）、
-- R7（security invoker 下必须变红 → 证明 security definer 承重）、
-- R8（把权限与策略都加回来即可插入 → 证明「插不进去」不是空转）。

-- ---------------------------------------------------------------- 权限层

revoke insert on public.style_config from authenticated;

-- anon 在建表时就已经被 revoke all，这里不重复；写出来只是为了让读者不必回查。
-- （20260907120000 的 `revoke all on public.style_config from anon, authenticated;`）

-- ---------------------------------------------------------------- 策略层

drop policy if exists style_config_insert_own on public.style_config;

-- select / update 两条策略**保持不动**——阶段 3 的页面靠它们工作。
-- delete 策略从来就没有（注销走 auth.users 的 on delete cascade，口径 F14）。

comment on table public.style_config is
  'F1 用户风格配置，一人一行。四条轴中仅 detail_level 被 A 类审查执行，'
  '其余三条（use_examples/structure/depth）仅进 prompt，enforced=false（F1方案 §2.2）。'
  '客户端权限：select / update 自己那行；**没有 insert**（2026-09-10 收回，'
  '建行只由 auth.users 的 after-insert 触发器完成）；没有 delete（注销走 cascade）。';
