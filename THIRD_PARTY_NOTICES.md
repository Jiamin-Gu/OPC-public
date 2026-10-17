# 第三方声明

## agency-agents-zh

`opc/role_prompts/` 下的角色提示词和审查规范参考了开源角色库 agency-agents-zh（agency-agents 的中文社区版），
按 OPC 的实际能力改写：保留关键规则、工作流程、交付物模板和检查清单，去掉了 AI 做不到的事和人设描述，
并为每个角色加上"做不到的事"。各文件参考的原角色见 `opc/role_prompts/SOURCES.md`。

`opc/role_library/`（角色库，约 270 个角色）和 `opc/task_templates/library/`（从原文拆出的交付物模板）直接来自该项目：
完整版是原文去掉头信息和交付物模板，简洁版由模型按 OPC 的能力改写，详见 `opc/role_library/SOURCES.md`。

- 项目：https://github.com/jnMetaCode/agency-agents-zh
- 上游：https://github.com/msitarzewski/agency-agents

```
MIT License

Copyright (c) 2025 Michael Sitarzewski (original English version)
Copyright (c) 2026 jnMetaCode (Chinese translation and localization)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
