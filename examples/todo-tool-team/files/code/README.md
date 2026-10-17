# todo CLI（第一版）使用说明

零依赖的命令行待办事项工具，单文件实现，数据保存为本地 JSON 文件。

- 需求文档：`docs/PRD.md`
- 技术方案：`docs/TECH_DESIGN.md`
- 源码：`code/todo.py`（仅用 Python 标准库）

> 实现说明（重要，请先看）：本版数据文件路径以 CEO 最新任务为准 —— **默认存当前目录 `./todos.json`，并支持 `--file` 指定**；同时保留 PRD 里的环境变量 `TODO_FILE` 兼容。
> 这与 `docs/PRD.md` §5.5 / AC-1 和 `docs/TECH_DESIGN.md` §6.1 记载的「默认 `~/.todo.json`、无 `--file`」**不一致**，差异见第 6 节，需 CEO / 产品经理同步文档。

## 1. 环境要求

- Python ≥ 3.8（无第三方依赖，无需 `pip install`）

## 2. 快速开始

在项目根目录执行（数据默认写到 **当前目录** 的 `todos.json`）：

```bash
python code/todo.py --help

python code/todo.py add "买牛奶"     # 已添加 #1: 买牛奶
python code/todo.py add "写周报"     # 已添加 #2: 写周报

python code/todo.py list
# 1. [ ] 买牛奶
# 2. [ ] 写周报

python code/todo.py done 1
# 已完成 #1: 买牛奶

python code/todo.py list
# 2. [ ] 写周报

python code/todo.py list --all
# 1. [x] 买牛奶
# 2. [ ] 写周报

python code/todo.py delete 2
# 已删除 #2: 写周报

python code/todo.py list
# 暂无待办事项
```

### 用 `--file` 指定数据文件（推荐用于试跑与测试隔离，避免污染当前目录）

```bash
python code/todo.py --file /tmp/todo-demo.json add "买牛奶"
python code/todo.py -f /tmp/todo-demo.json list
python code/todo.py add "写周报" --file /tmp/todo-demo.json   # 放在子命令后面也可以
```

`--file` 同时支持 `~` 展开与相对路径：

```bash
python code/todo.py --file ~/mine/todos.json list
python code/todo.py --file ./tmp/todos.json add "临时任务"
```

### 可选：安装为可直接执行的命令

```bash
chmod +x code/todo.py
ln -s "$(pwd)/code/todo.py" ~/.local/bin/todo   # 确保 ~/.local/bin 在 PATH 中
todo add "试试看"
```

注意：符号链接方式下依赖脚本的 shebang（`/usr/bin/env python3`），需保证能找到 Python 3.8+。

## 3. 命令一览

| 命令 | 说明 |
| --- | --- |
| `todo add <内容>` | 添加待办，输出 `已添加 #<id>: <内容>`；多个词可用空格分隔（建议加引号） |
| `todo list` | 列出未完成待办，格式 `<id>. [ ] <内容>` |
| `todo list --all` / `-a` | 列出全部，已完成显示为 `<id>. [x] <内容>` |
| `todo done <id>` | 标记完成，输出 `已完成 #<id>: <内容>`；已完成重复执行输出 `#<id> 已是完成状态` |
| `todo delete <id>` | 删除（无二次确认），输出 `已删除 #<id>: <内容>` |
| `--file/-f <路径>` | 指定数据文件（可放子命令前或后） |
| `todo --help` / `todo <子命令> --help` | 帮助，退出码 0 |

## 4. 数据文件

### 4.1 路径解析优先级（每次运行实时解析）

| 优先级 | 来源 | 规则 |
| --- | --- | --- |
| 1 | 命令行 `--file` / `-f` | 去空白后非空则使用；支持 `~` 与相对路径；传空值报用法错误（退出 2） |
| 2 | 环境变量 `TODO_FILE` | 去空白后非空则使用（兼容 PRD） |
| 3 | 默认 | **当前目录下的 `todos.json`** |

> `--file` 与 `TODO_FILE` 都不会修改当前工作目录；相对路径相对于「运行命令时的当前目录」。
> 目标目录不存在时，写操作（`add` / `done` / `delete`）会自动创建；只读命令（`list`）不会创建任何文件。

### 4.2 文件格式

UTF-8、`indent=2`、`ensure_ascii=False`（中文原样保存）、文件末尾恰好一个换行。

```json
{
  "version": 1,
  "next_id": 3,
  "todos": [
    {
      "id": 1,
      "text": "买牛奶",
      "done": false,
      "created_at": "2025-01-01T09:00:00",
      "done_at": null
    }
  ]
}
```

字段：`version`（固定 1）、`next_id`（下一个可用 ID，不复用已删除 ID）、`todos[].id/text/done/created_at/done_at`；时间格式 `YYYY-MM-DDTHH:MM:SS`（本地时间）。

### 4.3 写入安全

写入方式为「**同目录临时文件 + `fsync` + `os.replace` 原子替换**」：

- 任何时刻磁盘上的文件要么是完整旧内容、要么是完整新内容，不会写出半截 JSON。
- 读取失败（文件损坏）时**直接报错退出，绝不写回**，因此不会覆盖你已有的数据。
- POSIX 下文件权限为 `0600`（仅本人可读写）。

## 5. 退出码与异常处理

错误信息全部走 **stderr**，成功信息走 **stdout**，便于脚本 `2>/dev/null` 或做断言。

| 场景 | stdout / stderr | 退出码 |
| --- | --- | --- |
| 成功（含「已是完成状态」「暂无待办事项」） | stdout 正常信息 | 0 |
| 找不到 ID（`done` / `delete`） | stderr `错误：找不到 ID 为 <id> 的待办` | 1 |
| 内容为空（`add ""`） | stderr `错误：待办内容不能为空` | 2 |
| 内容超长（> 500 字符） | stderr `错误：待办内容过长（最多 500 个字符，当前 N 个）` | 2 |
| 缺子命令 / 缺参数 / 非法 id（`abc`、`0`、`-1`、`1.5`、全角数字）/ `--file` 为空 | stderr 帮助 + `错误：<原因>` | 2 |
| 数据文件损坏 / 版本不支持 / 无权限 / 路径是目录 / 写入失败 | stderr `错误：…，路径：<path>` | 3 |

补充说明：

- **空列表**：`list` 输出 `暂无待办事项`，退出码 0（不是错误）。
- **JSON 损坏**：`json` 解析失败、结构非法、ID 重复、版本 != 1 → 退出 3，且**不改动原文件**。
- **文件权限**：无读权限 → 退出 3；目录不可写 / 磁盘满 → 退出 3，且不会留下半写文件。
- **Ctrl+C**：stderr 打印 `错误：操作已取消`，退出码 130。
- **管道提前关闭**（如 `todo list | head -1`）：静默退出 0，不打印堆栈。

示例：

```bash
python code/todo.py done 99;      echo $?   # 错误：找不到 ID 为 99 的待办 / 1
python code/todo.py add "";       echo $?   # 错误：待办内容不能为空 / 2
python code/todo.py done;         echo $?   # 帮助 + 错误：缺少参数 <id> / 2
printf 'not json' > /tmp/bad.json
python code/todo.py -f /tmp/bad.json list; echo $?   # 错误：数据文件已损坏，路径：/tmp/bad.json / 3
```

## 6. 与 PRD / 技术方案的差异（待同步）

| 编号 | 事项 | PRD / 技术方案记载 | 本实现（按 CEO 最新任务） |
| --- | --- | --- | --- |
| C-1 | 数据文件默认路径 | `~/.todo.json` | **当前目录 `./todos.json`** |
| C-2 | 指定数据文件的方式 | 仅环境变量 `TODO_FILE` | **新增 `--file/-f`**；`TODO_FILE` 保留兼容 |
| C-3 | 优先级 | 仅 `TODO_FILE` | `--file` > `TODO_FILE` > `./todos.json` |

> 除上表外，其余（命令文案、退出码、数据模型、原子写）与 PRD 第 5、6 节保持一致。
> 影响：以 PRD §8 AC-1「断言 `~/.todo.json` 长度增加」编写的用例需改为断言 `./todos.json` 或显式用 `--file`/`TODO_FILE` 指向临时文件。**建议测试统一用 `--file` 指向临时文件做隔离。**
> 建议：请 CEO 拍板后，由产品经理更新 PRD §5.5 / AC-1、工程师更新 `docs/TECH_DESIGN.md` §4.1 / §6.1，使三份文档一致。

## 7. 已知限制

- 不做并发锁（第一版按 PRD 明确），两个命令同时执行时最坏丢一次更新，但文件不会损坏。
- 在 POSIX 上，若目标文件本身只读、但其所在目录可写，`os.replace` 仍能成功替换该文件（这是原子替换的固有行为，不是缺陷）。
- Windows 控制台若非 UTF-8 代码页，中文可能显示异常，可先设置 `set PYTHONIOENCODING=utf-8`。
- 内容长度上限 500 字符为硬校验（见 `docs/TECH_DESIGN.md` 第 10 节 D-1，待 CEO 确认）。

## 8. 给测试同学的建议

```bash
# 每例独立数据文件，隔离干净
export TODO_FILE="$(mktemp -d)/t.json"
python code/todo.py add "A"; echo $?   # 0
cat "$TODO_FILE"                       # 检查 JSON 内容

# 或用 --file（优先级最高，推荐）
python code/todo.py --file "$(mktemp -d)/t.json" add "A"
```

- 断言 stdout / stderr / 退出码三者，文案以 `docs/PRD.md` 第 5、6 节为准（路径相关断言按第 6 节调整）。
- 也可直接 `import todo` 调 `cmd_add` / `cmd_list` / `cmd_done` / `cmd_delete` / `normalize_state` 做快速单元测试（命令层不碰文件系统、不打印）。
- 覆盖点建议：空列表、非法 ID（`abc`/`0`/`-1`/`1.5`/全角）、JSON 损坏（断言原文件字节不变）、无权限文件、`--file` 前后位置、`--file` 传空值、`TODO_FILE` 与 `--file` 同时存在时的优先级。
