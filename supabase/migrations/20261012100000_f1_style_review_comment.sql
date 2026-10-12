-- 2026-10-12 · 只改表注释：B 类已接入 LLM 审查。
-- 不动任何列、约束、权限、策略；与 20260910090000 里的注释只差第一句。

comment on table public.style_config is
  'F1 用户风格配置，一人一行。B 类已接入 LLM 审查。'
  '客户端权限：select / update 自己那行；**没有 insert**（2026-09-10 收回，'
  '建行只由 auth.users 的 after-insert 触发器完成）；没有 delete（注销走 cascade）。';
