# 角色库出处

`role_library/` 里的角色全部来自 [agency-agents-zh](https://github.com/jnMetaCode/agency-agents-zh)（MIT，见仓库根目录的 THIRD_PARTY_NOTICES.md），
每个角色在 `index.json` 的 `source` 字段记着它在原项目里的路径。用 `scripts/import_role_library.py` 导入，原项目更新后可以重新导入（已经改写好的简洁版会保留）。

- `<部门>/<角色>.complete.md`：完整版 = 原文，只去掉了开头的头信息和交付物模板章节
- `<部门>/<角色>.concise.md`：简洁版，由 `scripts/rewrite_concise.py` 用模型（deepseek-v4-flash）按 6 个预设角色的写法改写：
  一段定位 + 关键规则 + 工作流程 + 和同事协作 + 你做不到的事，按 OPC 里 AI 同事的实际能力改写，不含任何产出格式。
  改写结果过了机械检查（必须有的章节、长度、没有代码块和表格、不写代码的角色不能提编码 agent），个别不合格的经人工修正
- 交付物模板不放在角色里：拆到了 `../task_templates/library/<角色>.md`，公司里有这个角色时规划助手可以给任务选用（名字写成 `role:<角色 id>`）

6 个预设角色（产品经理、工程师、设计师、测试、市场、运营）是逐个手工改写的，在 `../role_prompts/`，出处见那里的 SOURCES.md。
