# todo — 命令行待办工具

`todo` 是一个迷你命令行待办清单，所有数据保存在一个 JSON 文件里。它支持四个命令：

| 命令 | 作用 |
| --- | --- |
| `todo add TEXT...` | 新增一条待办 |
| `todo list [--all]` | 列出待办（默认只看未完成） |
| `todo done ID` | 把某条待办标记为完成（幂等） |
| `todo delete ID` | 删除某条待办 |

设计说明见 [`DESIGN.md`](DESIGN.md)，需求契约见 [`../docs/PRD.md`](../docs/PRD.md)。

## 依赖

- 运行：**Python 3.9+，零第三方运行时依赖**（只用标准库：`argparse` / `json` / `pathlib` / `os` / `tempfile` / `datetime` / `sys`）。
- 测试：需要 `pytest`（`pip install pytest`）。

## 安装

方式一：安装成命令（推荐），安装后可直接使用 `todo`：

```console
$ cd code
$ pip install -e .
$ todo --version
todo 0.1.0
```

方式二：不安装，直接用模块方式运行（把 `todo` 换成 `python -m todo_cli` 即可）：

```console
$ cd code
$ python -m todo_cli --version
todo 0.1.0
```

> 本文档后续示例统一写作 `todo`，若未安装请自行替换为 `python -m todo_cli`。

## 数据文件放在哪里

路径按以下优先级解析（`~` 会被展开）：

1. 全局选项 `--file PATH`（必须写在子命令**之前**）；
2. 环境变量 `TODO_FILE`；
3. 默认 `~/.todo.json`。

```console
$ todo --file ./todos.json add buy milk        # 只用这一次文件
$ TODO_FILE=./todos.json todo add buy milk     # 用环境变量
$ todo add buy milk                            # 用 ~/.todo.json
```

数据文件的格式（UTF-8、`indent=2`、结尾带换行）：

```json
{
  "version": 1,
  "next_id": 4,
  "todos": [
    {
      "id": 1,
      "text": "买牛奶",
      "done": false,
      "created_at": "2026-10-01T16:13:57Z"
    }
  ]
}
```

`id` 只增不复用：`next_id` 是下一条待办要用的编号，删除待办不会回收编号。
文件中出现的未知字段（顶层或单条待办里的）在写回时原样保留。

## 用法与示例

以下输出在本机（Windows + Python 3.10.6）实际运行并逐字核对过（示例按同一会话的先后顺序排列）。

### add

```console
$ todo add 买牛奶
Added #1: 买牛奶
$ todo add 写周报
Added #2: 写周报
$ todo add 给 orders 表加索引        # 多个词会用一个空格拼起来
Added #3: 给 orders 表加索引
```

文本会先清洗：去掉首尾空白，把中间的连续空白（含换行、制表符）折叠成一个空格，
所以 `todo add buy    milk` 得到 `buy milk`。清洗后为空则是用法错误：

```console
$ todo add ""
Error: todo text cannot be empty.        # 退出码 2（错误信息走 stderr，stdout 为空）
```

同样地，`todo add`（没给文本）也由 argparse 报用法错误，退出码 2。

### list

```console
$ todo list
1. [ ] 买牛奶
2. [ ] 写周报
$ todo done 2
Completed #2: 写周报
$ todo list                 # 默认只看未完成
1. [ ] 买牛奶
$ todo list --all           # -a 等价
1. [ ] 买牛奶
2. [x] 写周报
```

没有待办时：

```console
$ todo list
No pending todos.
$ todo list --all          # 一条都没有（包括已完成的）
No todos yet.
```

`list` 永远只读：文件不存在时它也**不会**创建文件、不会改动文件内容或修改时间。

### done

```console
$ todo done 2
Completed #2: 写周报
$ todo done 2              # 幂等：不报错、不写盘
Todo #2 is already completed.
$ todo done 99
Error: todo #99 not found.         # 退出码 1
```

### delete

```console
$ todo delete 2
Deleted #2: 写周报
$ todo delete 2
Error: todo #2 not found.          # 退出码 1
$ todo delete abc
Error: 'abc' is not a valid todo id.   # 退出码 2
```

删除不会回收编号：

```console
$ todo add 新任务
Added #4: 新任务
```

### 帮助

```console
$ todo --help
usage: todo [-h] [--file PATH] [--version] {add,list,done,delete} ...
...
commands:
  {add,list,done,delete}
    add                 Add a new todo.
    list                List pending todos (use --all for every todo).
    done                Mark a todo as completed (idempotent).
    delete              Delete a todo.
```

每个子命令都有 `-h`，包含说明、参数说明和示例，例如：

```console
$ todo add -h
usage: todo add [-h] TEXT [TEXT ...]
...
Example:
  todo add buy milk
  Added #1: buy milk
```

## 退出码

| 退出码 | 含义 | 例子 |
| --- | --- | --- |
| `0` | 成功 | `add` / `list` / `done` / `delete` 正常完成；`done` 重复执行也算成功 |
| `1` | 运行时错误（数据文件读写或找不到待办） | `Error: todo #99 not found.`、`Error: invalid todo file <abs>:`、`Error: cannot write todo file <abs>:` |
| `2` | 用法错误（参数不合法） | 没有子命令、未知子命令、`todo add`（缺少文本）、`todo add ""`、`todo done abc`、`todo done 0` |

约定：成功信息写 **stdout**，所有错误信息写 **stderr**；出错时 stdout 为空。

## 跑测试

```console
$ cd code
$ python -m pytest -q
```

本次实际结果（Windows，Python 3.10.6，pytest 9.1.1）：

```text
$ python -m pytest -q
........................................................................ [ 73%]
..........................                                               [100%]
98 passed in 1.55s
```

98 个用例分布：`tests/test_cli.py` 59（覆盖 PRD 第 8 节全部 AC-ADD / AC-LIST / AC-DONE /
AC-DEL / AC-G / AC-ERR，外加数据完整性与 subprocess 端到端用例）、
`tests/test_storage.py` 29、`tests/test_models.py` 10。
没有 skip、没有 xfail；在 root 下运行时 `AC-ERR-4`（只读文件）会自动 skip 并注明原因。

测试全部在 `tmp_path` 里跑，绝不会读写真实的 `~/.todo.json`（有 autouse 守卫 fixture 兜底），
测试结束后也不会留下真实的 `~/.todo.json`。

## 已知限制

- **不做文件锁**：两个进程同时写同一个数据文件时，后写的那次会覆盖前一次的修改（原子写保证文件不会损坏，但不保证不丢失并发更新）。
- **未实现 P1 功能**：彩色输出、`list --done`（只看已完成）、数据文件 `0600` 权限、过长文本截断。
- 空文件（0 字节）会被当成空清单处理；文件内容不是合法 JSON、`version` 不是 `1`、或结构不符合约定时，会报 `Error: invalid todo file ...` 并且**不修改原文件**。
- 目标目录不存在时不会自动创建，会报 `Error: cannot write todo file ...`。

## 本次验收环境与残余风险

本次实现与验收在 **Windows 10 + Python 3.10.6 + pytest 9.1.1** 上完成：`cd code && python -m pytest -q` 结果 98 passed，无 skip；另外用 `python -m todo_cli --file <临时文件> ...` 逐条复核了四个命令的退出码与文案。

需要注意的两点：
1. **Linux / macOS 未在本机实测**。PRD 第 9 节要求 Linux / macOS 必须通过，但本机只有 Windows。实现刻意只用了跨平台 API（`pathlib`、`os.replace`、`tempfile.mkstemp`、`os.access`），未使用 `fcntl`、`/dev/*` 等 Unix-only 接口；仍建议在 Linux / macOS 上跑一次 `cd code && python -m pytest -q` 做最终确认。
2. **Python 3.9 只做了静态语法核查**。本机无 3.9 解释器，已用 `ast.parse(..., feature_version=(3, 9))` 逐文件确认 `code/todo_cli/*.py` 与 `code/tests/*.py` 可被 3.9 语法解析，且未使用 `tomllib`、`str.removeprefix` 等 3.10+ API；注解一律写成 `# type:` 注释式。建议在 3.9 环境补跑一次测试。
