# 成果示例

OPC 的真实运行记录（模型 `deepseek-flash`）。每个示例包含：

- `files/`：公司共享文件区的全部产出（文档和代码），都是 AI 团队写的
- `transcript.md`：对话记录：CEO 指令、执行计划、每个角色的汇报、最终的执行汇报、各角色的 token 用量
- `README.md`：过程和结论

| 示例 | 版本 | 团队 | 结果 |
|---|---|---|---|
| [todo-tool-team](todo-tool-team/README.md) | v0.1 | 产品经理、工程师、测试 | 6 份产出；代码能运行，测试在审查中发现了一个真实缺陷 |
| [todo-tool-with-coding-agent](todo-tool-with-coding-agent/README.md) | v0.2 | 产品经理、工程师（接入编码 agent） | 工程师交付的 98 个测试全部通过；暴露了编码任务成本高的问题 |
| [prompt-levels-ab](prompt-levels-ab/README.md) | v0.6 | 产品经理、设计师、工程师、测试 | 简洁版 / 极简版 / 完整版提示词对比：简洁版成本最低、返工最少；极简版返工多反而最贵（之后去掉了）；三版都没有编造证据 |
| [review-rules-ab](review-rules-ab/README.md) | v0.6.2 | 产品经理、设计师、工程师、测试 | 审查规范加入"场景推演"前后对比（新题：健身房团课预约）：审查从查一致性变成找规则漏洞，最终 PRD 难点覆盖 18/20 vs 14/20，成本没有增加 |
| [edit-rework-pilot](edit-rework-pilot/README.md) | v0.7.0 | 产品经理、设计师、工程师、测试 | 返工改用局部修改：119 次修改全部成功、没有整份重写；但一次只改一处导致调用次数大增，下一步支持一次提交多处修改 |

导出自己公司的成果：

```bash
python -m opc.export 公司id --out 输出目录
```

公司 id 可以在 `~/.opc/companies/` 下看到（每个文件夹名就是一个公司 id）。
