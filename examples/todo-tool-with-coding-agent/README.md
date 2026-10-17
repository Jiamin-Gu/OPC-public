# 示例：工程师借助编码 agent 写出并跑通代码（v0.2）

OPC v0.2 的真实运行（模型 `deepseek-flash`），工程师接入了 [Coding-agent](https://github.com/Jiamin-Gu/Coding-agent-public)，公司开启了"允许工程师运行代码"。

- **团队**：📋 产品经理、💻 工程师
- **CEO 的指令**：做出第一版命令行待办工具：产品经理先写需求文档；工程师据此实现 Python 代码（支持添加、列出、完成、删除待办，数据存到 JSON 文件），并编写 pytest 测试，确保全部测试通过

## 过程

1. 规划助手知道工程师能运行代码，在任务里明确要求"实际运行 pytest，确保全部测试通过"
2. 产品经理写出 PRD，并给工程师写好接口约定
3. 工程师读完 PRD，先写技术方案，再把实现交给编码 agent；编码 agent 先检查本机的 Python 和 pytest 版本，再写代码和测试。工程师拿到结果后又两次委派编码 agent 做独立复核
4. 约 10 分钟完成，98 个测试全部通过
5. 之后 CEO 单独找工程师："写一个 fizzbuzz 并用 pytest 测试"，72 秒完成

## 产出（[files/](files/)）

| 文件 | 作者 | 内容 |
|---|---|---|
| `docs/PRD.md` | 产品经理 | 需求、验收标准、给工程师的接口约定 |
| `code/DESIGN.md` | 工程师 | 技术方案 |
| `code/todo_cli/`（4 个模块） | 工程师（编码 agent） | 命令行待办工具 |
| `code/tests/`（3 个文件） | 工程师（编码 agent） | 98 个测试用例 |
| `code/README.md`、`code/pyproject.toml` | 工程师（编码 agent） | 安装、用法、跑测试的方法 |
| `code/fizzbuzz.py`、`code/tests/test_fizzbuzz.py` | 工程师（编码 agent） | 单独沟通时的小任务，36 个测试用例 |

## 对话记录

[transcript.md](transcript.md)：CEO 指令、执行计划、产品经理和工程师的汇报、最终的执行汇报，以及 fizzbuzz 的单独沟通。

**成本**：工程师（含编码 agent）调用模型 96 次、输入约 274 万 token，是这次运行暴露出的主要问题。编码 agent 每次调用都带着完整上下文，而且倾向于反复验证。这个问题促成了后续在 Coding-agent 里建评测集、token 账本和压力测试的工作。

## 运行测试

```bash
cd files/code
python -m pytest -q
```

134 个测试用例全部通过（待办工具 98 个，fizzbuzz 36 个）。
