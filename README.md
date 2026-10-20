# OPC：一人公司

[![tests](https://github.com/Jiamin-Gu/OPC-public/actions/workflows/tests.yml/badge.svg)](https://github.com/Jiamin-Gu/OPC-public/actions/workflows/tests.yml)

基于 **LangGraph** 的多角色 AI 协作平台。你是 CEO：选好团队成员，给全公司下一条指令，规划助手把它拆成带依赖关系的任务，产品经理、工程师、设计师、测试等角色 agent 并行执行、互相审查，产出需求文档、技术方案、代码和测试计划。执行中你随时可以插话、让某个任务返工，下游会自动跟上。

**当前版本：v0.22.0** · [更新记录](CHANGELOG.md) · [使用教程](docs/usage.md) · [架构与设计决策](docs/design.md) · [真实运行示例](examples/README.md)

## 亮点

- **事件驱动的调度**：调度图每来一个事件（下令、审批、任务完成、变更、裁决）运行一次，用 `Command` 路由；任务一满足依赖就派发，支持"完成后开始"和"开始后开始"两种依赖
- **可暂停、可恢复的角色**：每个角色的执行循环也是一张 LangGraph 图，每一步一个检查点；用 `interrupt` 暂停，恢复时带着原对话接着做，服务重启后也能续跑
- **跟谁说都一样**：在全公司、任何成员的私聊或项目经理频道里说"上游那份不对"，调度助手都整理成同一组调整（谁返工、哪些下游暂停），确认后执行，下游带着改动对比同步
- **机械验收，而不是让模型自评**：规划时生成交付要求和验收标准，提交时逐条检查；审查意见必须引用原文并由系统核对，复审只看上一轮的问题和本次改动，收敛后收尾复审
- **小事快、大事稳**：一个人一次能做完的小任务不用审批、不安排审查，几十秒出结果；大任务走完整的规划、审查、返工流程，审查谈不拢时交给 CEO 裁决
- **长期记忆（RAG）**：每家公司一个知识库，收录文档、代码、任务记录、每条指令的结论，以及你上传的 PDF / Word 资料；BM25 + 向量混合检索（人看的搜索再加重排序），结果带出处（精确到文件行号或资料的页）。角色干活时按需检索，规划新指令时自动召回以前定下的口径，编码 agent 也能查
- **执行链路**：每条指令从规划到汇报的每一步（每次模型调用、每次工具调用）在瀑布图上看得到：用时、token、首字延迟、哪里出错；可以按 OpenTelemetry 标准导出到 Jaeger、Langfuse
- **成本看得见**：每条指令、每个任务的 token 用量都摆出来，可以按价格估算花费；"投入档位"按审查轮数和执行步数封顶，而不是按 token 截断
- **角色库**：6 个预设角色，另有 277 个按部门分组的专业角色可选（提示词来自开源角色库 agency-agents-zh，接上 OPC 里 AI 能力边界的说明）
- **模型、工具都可以换**：任何 OpenAI 兼容接口都能用，任何 MCP 服务器的工具都能接给角色；模型连接配置一次，公司选默认模型，个别成员可以单独换
- **工程**：215 个自动化测试（用脚本化的假模型，不消耗 API）；SQLite 持久化，关掉再打开对话、任务、待审批的计划都在

## 快速开始

需要 Python 3.10+、Git，以及一个兼容 OpenAI 协议的模型 API（例如 [DeepSeek](https://platform.deepseek.com/)）。没有 API 也可以先用演示模式看效果。

```bash
git clone https://github.com/Jiamin-Gu/OPC-public.git
cd OPC-public
```

- **Windows**：双击 `start.bat`。第一次运行会安装依赖、创建 `.env` 并用记事本打开，填好模型配置后保存关闭即可，浏览器自动打开 http://127.0.0.1:8780
- **macOS / Linux**：`bash start.sh`

也可以手动启动：

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows 用 copy；填写 OPC_API_KEY 等
python -m opc.web           # 加 --demo 进入演示模式（假模型，不需要 API）
```

模型也可以不写进 `.env`：启动后网页会提示你添加（首页"⚙ 管理模型连接"）。

## 可选组件

都不装也能用，只是少了对应的能力：

- **编码 agent**：工程师把写代码、运行和测试交给 [Coding-agent](https://github.com/Jiamin-Gu/Coding-agent-public)（作者的另一个开源项目）。`start.bat` / `start.sh` 会自动尝试安装（需要 Git），也可以手动 `pip install -r requirements-coder.txt`。没装的话工程师只写代码、不运行
- **公司知识库的本地向量模型**：`pip install -r requirements-kb.txt`，第一次使用时下载模型（约 95 MB）。不装的话知识库只用关键词检索
- **联网**（搜索、读网页等）：角色的联网工具来自 MCP 服务器，在 `~/.opc/mcp.json` 里配置，格式是通用的 MCP 配置格式（`mcpServers`，Cursor、Claude 等工具都用这种），支持本机启动的服务器和远程地址。比如装上官方的网页读取服务器（`pip install mcp-server-fetch`），写进配置，角色就能读网页了。没有配置时创建公司界面的"允许联网"是灰的，其他功能不受影响，配置方法见 [使用教程](docs/usage.md#联网调研)

## 用起来是什么样

1. **创建公司**：填公司名称和目标，选默认模型和团队成员（可以从角色库里按部门挑）
2. **下令**：在「全公司」频道说一句话，比如"做一款健身房团课预约小程序，先出需求、设计、技术方案和测试计划"。规划助手给出计划，你批准后各成员并行开工；小事（"搜一下……"）直接开始
3. **看进度、随时干预**：每个任务一张进展卡片；在任何频道说"这里不对"，都会变成调整建议，你确认后执行
4. **拿成果**：最终汇报附上本次所有成果，点开直接阅读；也可以单独找某位成员深入讨论某项工作

详细说明见 [使用教程](docs/usage.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [使用教程](docs/usage.md) | 配置、创建公司、下令、审查和返工、插话和调整、协作、成果导出、联网、知识库、命令参数 |
| [架构与设计决策](docs/design.md) | 调度图和执行引擎、状态和消息的存储、模块说明、每个关键设计为什么这样做 |
| [实测、已知限制与路线图](docs/results.md) | 早期的真实运行数据、目前的限制、后续计划 |
| [真实运行示例](examples/README.md) | 多次真实运行的完整产出、对话记录和对比实验 |
| [更新记录](CHANGELOG.md) | 每个版本改了什么、为什么改 |

## 运行测试与发布

```bash
pip install pytest
python -m pytest            # 215 个测试，用假模型，不调用 API
```

- **CI**：每次推送和 PR，GitHub Actions 在 Ubuntu（Python 3.10 / 3.12）和 Windows（Python 3.12）上跑全部测试（`.github/workflows/tests.yml`）
- **CD**：推送版本标签（例如 `v0.21.0`）时自动发布（`.github/workflows/release.yml`）：跑全部测试 → 检查标签、代码里的版本号和 CHANGELOG 一致（`scripts/release_check.py`）→ 打包 → 在全新的虚拟环境里装好安装包做冒烟测试（`scripts/smoke_installed.py`）→ 创建 GitHub Release，说明取自 CHANGELOG 里这一版的内容，附上安装包。任何一步不通过就停下、不发布。试跑可以用 `v0.21.0-rc.1` 这样的标签（发布成预发布）

## 许可证与致谢

[MIT](LICENSE)。角色提示词和交付物模板参考了开源角色库 [agency-agents-zh](https://github.com/jnMetaCode/agency-agents-zh)（MIT 许可证），见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
