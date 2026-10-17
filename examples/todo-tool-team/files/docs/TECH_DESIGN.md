# 技术方案：命令行待办事项工具（todo CLI v1）

- 文档作者：💻 工程师
- 版本：v1.0
- 状态：待 CEO 与 🧪 测试评审
- 上游依据：`docs/PRD.md`（产品经理，v1.0）
- 关联产出：`code/todo.py`（本方案的参考实现）、`code/README.md`（运行说明）
- 命名说明：PRD 第 12 节把技术方案记为 `docs/DESIGN.md`，按 CEO 指令本文件名为 `docs/TECH_DESIGN.md`，两者指同一份文档，以本文件为准。

---

## 1. 方案总览

### 1.1 交付目标（对齐 PRD 第 1、4 节）

用**单个 Python 文件 + 纯标准库**实现四条核心命令（`add` / `list` / `done` / `delete`），数据持久化到本地 JSON 文件，无需任何安装步骤即可 `python todo.py ...` 运行。

### 1.2 设计原则

| 编号 | 原则 | 理由 |
| --- | --- | --- |
| P1 | 零第三方依赖，只用标准库 | PRD 第 9 节；服务器/容器里 `python todo.py` 直接可用 |
| P2 | 单文件交付，文件内部分层 | PRD 要求交付形态为单文件；分层保证可读、可单测 |
| P3 | 进程模型极简：一条命令一个进程，读 → 改 → 写 | 无守护进程、无锁、无状态残留 |
| P4 | 数据安全优先：宁可报错，绝不写坏已存在的文件 | 用户数据是唯一不可再生资产 |
| P5 | 输出文案与退出码视为**公开接口**，逐字对齐 PRD 5.6 / 6.2 / 6.3 | 测试用它们做断言 |
| P6 | 可测试性：文件路径、当前时间、输出通道都可注入或替换 | 测试可用 `TODO_FILE` 隔离，无需真实家目录 |

### 1.3 明确不做（对齐 PRD 第 10 节）

不做多清单、云同步、GUI/TUI、提醒、搜索排序、导入导出、二次确认、并发锁、配置命令。

---

## 2. 技术选型与运行环境

| 项目 | 选型 | 理由 |
| --- | --- | --- |
| 语言版本 | **Python ≥ 3.8** | PRD 第 9 节；3.8 起 `pathlib`、`dataclasses` 级 API 稳定，`os.replace` 全平台可用 |
| 依赖 | **无第三方依赖**，仅标准库：`argparse`、`json`、`os`、`sys`、`tempfile`、`datetime`、`pathlib`、`typing` | 零安装、零供应链风险 |
| CLI 框架 | `argparse`（不用 click / typer） | 标准库；`--help` 与退出码 2 的默认行为正好贴合 PRD |
| 存储 | 单个 JSON 文件（不用 SQLite） | PRD 要求「纯文本可读」；千条规模下 JSON 全量读写 < 200ms |
| 写入方式 | 临时文件 + `os.fsync` + `os.replace` 原子替换 | 任何时刻磁盘上的文件要么是旧内容、要么是新内容，不会半写 |
| 时间 | `datetime.now().replace(microsecond=0).isoformat()`，本地时间、无时区 | PRD 5.5 规定 `YYYY-MM-DDTHH:MM:SS`；字符串可直接比较先后 |
| 平台 | 主支持 Linux / macOS / WSL；Windows 可用（注意事项见 7.6） | 目标用户是终端开发者 |
| 运行方式 | `python todo.py <命令>`；可选 `chmod +x todo.py` 后 `./todo.py` | 无需打包、无需 pip install |

---

## 3. 架构与模块划分

### 3.1 分层结构

```
             ┌──────────────────────────────────────────────┐
 argv ──────▶│ ① CLI 层  build_parser / TodoArgumentParser   │ 参数定义、帮助、用法校验
             └───────────────┬──────────────────────────────┘
                             ▼
             ┌──────────────────────────────────────────────┐
             │ ② 命令层  cmd_add / cmd_list / cmd_done /     │ 纯业务：只操作内存中的 state
             │           cmd_delete / validate_text /        │ 不接触文件、不打印
             │           parse_todo_id / find_todo           │
             └───────────────┬──────────────────────────────┘
                             ▼
             ┌──────────────────────────────────────────────┐
             │ ③ 存储层  load_state / normalize_state /      │ 文件 IO、校验、原子写
             │           save_state / resolve_data_path      │
             └───────────────┬──────────────────────────────┘
                             ▼
             ┌──────────────────────────────────────────────┐
             │ ④ 基础设施  TodoError / 退出码常量 /           │ 被以上各层共用
             │             now_string / main / run           │
             └──────────────────────────────────────────────┘
```

**依赖方向严格单向**：①→②→③→④，④不反向依赖任何层。

> 关键约束：**② 命令层不 import 任何文件 IO**，只接收 `state` 字典并返回一条提示文案；③ 存储层不打印、不 `sys.exit`，只抛 `TodoError`。这样测试可以脱离文件系统直接测业务逻辑。

### 3.2 模块与函数清单（全部落在 `code/todo.py`）

| 名称 | 层 | 职责 | 输入 → 输出 |
| --- | --- | --- | --- |
| `DATA_VERSION` | ④ 常量 | 数据格式版本，固定 `1` | — |
| `DEFAULT_FILENAME` | ④ 常量 | `".todo.json"` | — |
| `ENV_VAR` | ④ 常量 | `"TODO_FILE"` | — |
| `MAX_TEXT_LENGTH` | ④ 常量 | `500` | — |
| `EXIT_OK / EXIT_NOT_FOUND / EXIT_USAGE / EXIT_DATA` | ④ 常量 | 退出码 `0 / 1 / 2 / 3` | — |
| `TodoError` | ④ | 业务异常，携带 `message` 与 `exit_code` | `(str, int) → 异常对象` |
| `TodoArgumentParser` | ① | 继承 `argparse.ArgumentParser`，重写 `error()`：帮助打 stderr + 中文错误 + 退出 2 | — |
| `build_parser()` | ① | 构建顶层解析器与 4 个子解析器（含 metavar 与 `--all`） | `→ ArgumentParser` |
| `usage_error(parser, msg)` | ① | 打印帮助到 stderr 后抛 `TodoError(msg, 2)` | `(parser, str) → NoReturn` |
| `parse_todo_id(raw, parser)` | ② | 校验 `<id>` 为 ASCII 正整数，否则用法错误退出 2 | `(str, parser) → int` |
| `validate_text(text)` | ② | 校验内容非空且 ≤ 500 字符，否则退出 2 | `str → None` |
| `find_todo(state, id)` | ② | 按 ID 查找待办 | `(state, int) → dict \| None` |
| `cmd_add(state, text)` | ② | 分配 `next_id`、追加待办、递增 `next_id` | `(state, str) → 提示文案` |
| `cmd_list(state, show_all)` | ② | 按 ID 升序生成清单文本，或「暂无待办事项」 | `(state, bool) → 文案` |
| `cmd_done(state, id)` | ② | 置 `done=True` + `done_at`；重复完成返回「已是完成状态」；找不到抛退出码 1 | `(state, int) → 文案` |
| `cmd_delete(state, id)` | ② | 从列表移除；找不到抛退出码 1 | `(state, int) → 文案` |
| `resolve_data_path(environ)` | ③ | 运行时解析数据文件路径（`TODO_FILE` 优先，其次 `~/.todo.json`） | `→ Path` |
| `new_state()` | ③ | 空数据：`{"version":1,"next_id":1,"todos":[]}` | `→ dict` |
| `load_state(path)` | ③ | 读文件 + 解析 + 归一化；不存在/空文件 → 空数据；损坏 → 抛退出码 3 | `Path → dict` |
| `normalize_state(data, path)` | ③ | 结构校验、字段类型校验、去重 ID、修正 `next_id` | `(Any, Path) → dict` |
| `save_state(path, state)` | ③ | 建目录 → 临时文件写入 → `fsync` → `os.replace` 原子替换 | `(Path, dict) → None` |
| `now_string()` | ④ | 当前本地时间字符串 | `→ str` |
| `main(argv)` | ④ | 编排：解析参数 → 校验 → 读 → 改 → 写 → 打印 → 返回退出码 | `(list\|None) → int` |
| `run()` | ④ | 进程入口：捕获 `KeyboardInterrupt`（130）与 `BrokenPipeError`（0），`sys.exit(code)` | `→ NoReturn` |

### 3.3 一次命令的完整执行链路（以 `todo done 3` 为例）

1. `main` 调 `build_parser().parse_args()`；语法错误由 `TodoArgumentParser.error` 处理：帮助 → stderr，`错误：…` → stderr，退出码 2。
2. `parse_todo_id("3", parser)` 校验 id 为正整数，非法则打印帮助 + `错误：<id> 必须是正整数…`，退出码 2。
3. `resolve_data_path()` 决定数据文件（`TODO_FILE` 非空则用它，否则 `~/.todo.json`）。
4. `load_state(path)`：文件不存在或为空 → 空数据；JSON 非法 / 结构非法 → 抛 `TodoError(exit_code=3)`，**直接结束，不写文件**。
5. `cmd_done` 在内存 state 中把该条置完成并写 `done_at`，返回 `已完成 #3: …`。
6. `save_state`：同目录建临时文件 → 写入 JSON → `fsync` → `os.replace` 覆盖目标文件。
7. stdout 打印文案，返回退出码 0。
8. 任何一步抛 `TodoError` → 统一在 `main` 捕获 → 消息写 stderr → 返回该异常的退出码。

---

## 4. 命令行参数设计

### 4.1 命令与选项

| 命令 | 位置参数 / 选项 | 说明 |
| --- | --- | --- |
| `todo add <内容> [<内容> …]` | `text`（`nargs="+"`，metavar `<内容>`） | 添加待办；多个片段按空格拼接（见 10 节 D-2） |
| `todo list` | `-a` / `--all` | 列出待办；默认仅未完成 |
| `todo done <id>` | `id`（metavar `<id>`） | 标记完成 |
| `todo delete <id>` | `id`（metavar `<id>`） | 删除 |
| `todo --help` / `todo <子命令> --help` | argparse 自动生成 | 打印帮助，退出码 0 |

不实现 `--version`、短选项别名（除 PRD 允许的 `-a`）、配置文件与其它开关。

### 4.2 argparse 落地细节

| 关注点 | 做法 |
| --- | --- |
| 程序名 | `prog="todo"`，帮助里统一显示 `todo`，与 PRD 文档一致 |
| 缺子命令 | `add_subparsers(required=False)`（保持 3.8 兼容写法），由 `main` 判断 `args.command is None` → 帮助 + `错误：缺少子命令（可用：add、list、done、delete）`，退出 2 |
| 中文报错 | 自定义 `error()`，配翻译表把 argparse 英文消息转成 PRD 文案 |
| 「缺少参数 <id>」 | 位置参数设置 `metavar="<id>"`，argparse 的 required 消息即 `the following arguments are required: <id>`，翻译后得到 `错误：缺少参数 <id>`，与 PRD 6.3 逐字一致 |
| 未知参数 | `unrecognized arguments: xxx` → `错误：未知参数 xxx`，退出 2 |
| 无效子命令 | `invalid choice: 'xxx'` → `错误：无效的子命令：xxx`，退出 2 |
| id 校验 | argparse 只收字符串，正整数校验在 `parse_todo_id` 完成（`done -1`、`done abc`、`done 1.5`、`done ０` 全部拒绝） |
| 内容校验 | `" ".join(args.text).strip()` 后交给 `validate_text`：空 → `错误：待办内容不能为空`（退出 2）；超长 → `错误：待办内容过长（最多 500 个字符，当前 N 个）`（退出 2） |
| 帮助与错误通道 | `--help` 走 **stdout** 且退出 0；所有错误与错误场景下的帮助走 **stderr** |

### 4.3 输出文案契约（逐字，测试直接断言）

| 场景 | stdout | stderr | 退出码 |
| --- | --- | --- | --- |
| 添加成功 | `已添加 #<id>: <内容>` | 空 | 0 |
| 列出（有未完成） | `<id>. [ ] <内容>` 每行一条，ID 升序 | 空 | 0 |
| 列出（`--all` 含已完成） | 已完成行为 `<id>. [x] <内容>` | 空 | 0 |
| 列表为空 | `暂无待办事项` | 空 | 0 |
| 完成成功 | `已完成 #<id>: <内容>` | 空 | 0 |
| 重复完成 | `#<id> 已是完成状态` | 空 | 0 |
| 删除成功 | `已删除 #<id>: <内容>` | 空 | 0 |
| ID 不存在 | 空 | `错误：找不到 ID 为 <id> 的待办` | 1 |
| 内容为空 | 空 | `错误：待办内容不能为空` | 2 |
| 参数缺失/非法 | 空 | 帮助信息 + `错误：<原因>` | 2 |
| 数据文件损坏 | 空 | `错误：数据文件已损坏，路径：<path>` | 3 |

### 4.4 退出码语义

| 码 | 含义 | 触发方 |
| --- | --- | --- |
| 0 | 成功（含「重复完成」「空清单」这类正常情况） | 所有成功路径、`--help` |
| 1 | 业务查找失败：ID 不存在 | `cmd_done`、`cmd_delete` |
| 2 | 用法错误：子命令缺失、参数缺失/非法、内容为空或超长 | CLI 层、`validate_text`、`parse_todo_id` |
| 3 | 数据层错误：文件损坏、版本不支持、无权限、路径是目录、写失败 | 存储层 |

---

## 5. 数据模型与 JSON Schema

### 5.1 字段（对齐 PRD 5.5）

| 字段 | 类型 | 必填 | 约束与说明 |
| --- | --- | --- | --- |
| `version` | integer | 是 | 固定 `1` |
| `next_id` | integer | 是 | ≥ 1；下一个可用 ID，单调递增，不复用已删除 ID |
| `todos` | array | 是 | 可为空数组；按 `id` 升序落盘 |
| `todos[].id` | integer | 是 | ≥ 1，文件内唯一 |
| `todos[].text` | string | 是 | 已 `strip`，长度 1–500 |
| `todos[].done` | boolean | 是 | 是否完成 |
| `todos[].created_at` | string | 是 | `YYYY-MM-DDTHH:MM:SS`，本地时间 |
| `todos[].done_at` | string 或 null | 是 | 未完成时为 `null`，完成后为同一格式时间 |

### 5.2 JSON Schema（draft-07，供测试做结构校验）

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "todo data file",
  "type": "object",
  "required": ["version", "next_id", "todos"],
  "additionalProperties": false,
  "properties": {
    "version": {"const": 1},
    "next_id": {"type": "integer", "minimum": 1},
    "todos": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "text", "done", "created_at", "done_at"],
        "additionalProperties": false,
        "properties": {
          "id": {"type": "integer", "minimum": 1},
          "text": {"type": "string", "minLength": 1, "maxLength": 500},
          "done": {"type": "boolean"},
          "created_at": {
            "type": "string",
            "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$"
          },
          "done_at": {
            "type": ["string", "null"],
            "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$"
          }
        }
      }
    }
  }
}
```

> 说明：`write` 出来的文件一定满足该 schema；但 `read` 时对**可选字段缺失**更宽容，见 5.4（宽容读 + 严格写）。

### 5.3 落盘格式约定

- 编码 UTF-8，`ensure_ascii=False`（中文原样写、不转义为 `\uXXXX`）。
- `indent=2`，文件末尾恰好一个换行；便于 `git diff` 和人工阅读。
- 时间由 `now_string()` 生成，秒级精度；ISO 格式字符串可直接做先后比较。
- 文件权限：临时文件由 `tempfile.mkstemp` 创建（POSIX 下 0600），`os.replace` 后数据文件即为 `0600`（仅本人可读写）。这是对 PRD 未规定项的加固，见 10 节 D-4。
- 顶层对象只写 3 个字段，不再写额外字段（与 schema 的 `additionalProperties: false` 一致）。

### 5.4 读取时的容错规则（宽容读 / 严格写）

| 情况 | 处理 | 退出码 |
| --- | --- | --- |
| 文件不存在 | 视为空清单 | 0（后续命令正常执行） |
| 文件为空或全是空白字符 | 视为空清单（允许 `touch ~/.todo.json` 之后直接用） | 0 |
| JSON 解析失败 | `错误：数据文件已损坏，路径：<path>`，**不写文件** | 3 |
| 顶层不是对象 | 视为损坏 | 3 |
| `todos` 字段缺失 | 视为空数组 | 0 |
| `todos` 不是数组 | 视为损坏 | 3 |
| 某条待办 `id`/`text`/`done` 缺失或类型不符 | 视为损坏 | 3 |
| 两条待办 ID 重复 | 视为损坏（`错误：数据文件中存在重复 ID <id>，路径：<path>`） | 3 |
| `created_at` 缺失或类型不符 | 置为空字符串 `""`（不影响其它功能） | 0 |
| `done_at` 缺失或类型不符 | 置为 `null` | 0 |
| `version` 缺失 | 按 `1` 处理 | 0 |
| `version` 存在且 ≠ 1 | `错误：不支持的数据文件版本 <v>，路径：<path>` | 3 |
| `next_id` 缺失或 ≤ 最大 `id` | 修正为 `max(id) + 1`（兼容用户手工编辑文件） | 0 |
| 文件不是合法 UTF-8 | `错误：数据文件不是合法的 UTF-8 文本，路径：<path>` | 3 |
| 路径是目录 / 无读权限 / 其它 OSError | 对应中文错误 + 路径 | 3 |

### 5.5 ID 分配规则

- 唯一来源是 `next_id`：`add` 取当前值作为新 ID，然后 `next_id += 1`。
- 已删除的 ID **不回收、不复用**（PRD 5.4）。
- 读取时若发现 `next_id` 与现存 ID 冲突，自动修正为 `max(id) + 1`，保证后续 `add` 永不撞 ID。
- 读取时按 `id` 升序排序并落盘，因此无需担心文件里顺序错乱。

---

## 6. 文件读写与错误处理策略

### 6.1 路径解析（每次命令运行时解析）

| 优先级 | 来源 | 规则 |
| --- | --- | --- |
| 1 | 环境变量 `TODO_FILE` | 取值先 `strip()`；非空则使用，支持 `~` 展开（`expanduser`）与相对路径（相对当前工作目录） |
| 2 | 默认 | `Path.home() / ".todo.json"` |

- **运行时解析，不在模块导入时固化**：否则测试里改 `TODO_FILE` 无效（见 7.5）。
- `TODO_FILE` 为空字符串或只有空白 → 视为未设置，回退默认路径。
- 目标目录不存在时，`save_state` 会 `mkdir(parents=True, exist_ok=True)` 自动创建；`load_state` 不创建任何东西。

### 6.2 读策略

- 一次性整读（千条量级 < 1MB，性能足够），不做流式解析。
- 先判断存在性再读，并分别捕获 `FileNotFoundError` / `IsADirectoryError` / `PermissionError` / `UnicodeDecodeError` / `OSError`，各自映射为带路径的中文错误与退出码 3。
- **读取失败即终止**：`load_state` 抛异常时 `main` 直接返回退出码 3，绝不会走到 `save_state`，从而保证「损坏文件不被覆盖」（PRD AC-6）。

### 6.3 写策略：同目录临时文件 + `os.replace` 原子替换

1. `tempfile.mkstemp(prefix=".todo-", suffix=".tmp", dir=<目标文件所在目录>)` —— **必须同目录**，`os.replace` 才是同文件系统内的原子重命名。
2. `json.dump(..., ensure_ascii=False, indent=2)` 写完后 `flush()` + `os.fsync(fd)`，确保内容真正落盘。
3. `os.replace(tmp, target)`：POSIX 与 Windows 都是原子替换（Windows 上可覆盖已存在文件）。
4. 任何异常：`finally` 中删除临时文件，目标文件保持原样。
5. 写失败（磁盘满、无权限、目录只读）→ `错误：无法写入数据文件（<原因>），路径：<path>`，退出码 3。

> 效果：磁盘上的数据文件在任何时刻都是「完整的旧内容」或「完整的新内容」，不会出现半写 JSON。测试可断言「损坏文件在报错后内容字节级不变」。

### 6.4 写时机（哪些命令会写文件）

| 命令 | 是否写盘 | 说明 |
| --- | --- | --- |
| `add` | 是 | 成功添加后写；校验失败不写 |
| `done <id>` | 是 | 状态变化时写；**重复完成也写**（幂等重写，内容等价，便于实现统一） |
| `delete <id>` | 是 | 删除后写 |
| `list` | 否 | 纯只读，不因查看而修改文件（也不创建文件） |
| 出错路径 | 否 | 任何 `TodoError` 都在写盘之前抛出 |

### 6.5 错误分类与统一出口

- 全流程只用一种业务异常 `TodoError(message, exit_code)`；`message` 已含 `错误：` 前缀，直接打 stderr。
- 退出码常量集中在文件顶部，禁止散落魔数。
- `main` 是唯一的错误出口：`try/except TodoError` → 打印 + 返回退出码；不做 `sys.exit` 散点调用（argparse 的 `error()` 与 `run()` 例外，属于框架与入口边界）。

### 6.6 错误优先级

同一命令命中多个错误时的输出顺序（先校验「用户写错了」，再谈「数据有问题」）：

```
用法校验 (2)  →  数据文件读取 (3)  →  业务查找 (1)  →  写盘失败 (3)
```

例：`todo add ""` 且数据文件损坏 → 先报 `错误：待办内容不能为空`（2），因为读取发生在校验之后。

### 6.7 其它异常

| 异常 | 处理 |
| --- | --- |
| `KeyboardInterrupt`（Ctrl+C） | stderr `错误：操作已取消`，退出码 130 |
| `BrokenPipeError`（如 `todo list \| head -1`） | 静默退出 0，不打印堆栈 |
| 其它未预期异常 | 不吞掉；Python 默认打印 traceback 并退出 1（便于缺陷定位，v1 不做兜底包装） |

---

## 7. 关键难点与对策

| 编号 | 难点 | 对策 |
| --- | --- | --- |
| 7.1 | 输出文案与退出码就是对外接口，改一个字测试就红 | 文案在 PRD 4.3 表中冻结；实现里用同一批字面量常量/模板集中管理，测试按 PRD 逐字断言 |
| 7.2 | argparse 默认报错是英文、且格式与 PRD 不符 | 继承 `ArgumentParser` 重写 `error()`：帮助 → stderr、消息经翻译表转中文、退出 2；位置参数用 `metavar="<id>"` 让 required 消息天然变成 `缺少参数 <id>` |
| 7.3 | 写文件过程中崩溃会损坏用户数据 | 同目录临时文件 + `fsync` + `os.replace`；写失败清理临时文件；损坏文件绝不进入写路径 |
| 7.4 | `next_id` 与 `todos` 不一致（用户手工编辑过文件） | 读取时归一化：`next_id = max(next_id, max(id)+1)`，并在内存中按 ID 排序 |
| 7.5 | 测试隔离困难（默认写用户家目录） | 路径改为运行时解析 `TODO_FILE`；测试每例指向 `tmp_path`，绝不触碰真实 `~/.todo.json` |
| 7.6 | Windows 控制台默认编码非 UTF-8，中文可能乱码或 `UnicodeEncodeError` | 不调用 `sys.stdout.reconfigure()`（会破坏重定向/管道场景）；`code/README.md` 提示设置 `PYTHONIOENCODING=utf-8`，测试在 CI 中显式设置该变量 |
| 7.7 | 两个命令并发执行可能丢更新 | v1 按 PRD 明确不做锁；原子替换保证「不损坏、不半写」，最坏丢一次更新。已记录为已知限制 |
| 7.8 | 内容长度上限 | PRD 表述为「建议长度上限 500 字符」，本方案实现为硬校验（超长退出 2），需 CEO 确认（见 10 节 D-1） |
| 7.9 | 参数解析与业务逻辑耦合导致难测 | 强制分层：命令层函数只吃 `state` 字典、只吐文案，可脱离文件系统单测 |

---

## 8. 测试建议（给 🧪 测试）

### 8.1 双层测试策略

| 层次 | 方式 | 适合覆盖 |
| --- | --- | --- |
| 黑盒（主） | `subprocess.run([sys.executable, "code/todo.py", ...], env={..., "TODO_FILE": str(tmp_path/"t.json")}, capture_output=True, text=True)` | 验收标准 AC-1 ~ AC-7 全部：真实进程、真实退出码、真实 stdout/stderr |
| 白盒（辅） | `import` 模块，直接调 `cmd_add` / `cmd_list` / `cmd_done` / `cmd_delete` / `load_state` / `normalize_state` | 边界值密集场景（快，无需起进程）；`normalize_state` 的各种畸形数据 |

夹具要求：

- **必须**把 `TODO_FILE` 指向临时文件（`tmp_path`），禁止写真实家目录。
- 每个用例用独立数据文件，避免顺序耦合。
- 起进程时传完整环境变量副本（`{**os.environ, "TODO_FILE": ...}`）。

### 8.2 用例矩阵（AC → 用例映射）

| 用例组 | 覆盖点 | 关键断言 |
| --- | --- | --- |
| TC-A 添加 | `add A`、`add B`；多词内容；含中文/emoji；前后空白 | 分别返回 `已添加 #1: A` / `已添加 #2: B`；文件 `todos` 长度 +1、`next_id` 递增；`text` 已 strip |
| TC-B 添加边界 | `add ""`、`add "   "`、500 字符、501 字符 | 空/空白 → stderr `错误：待办内容不能为空`、退出 2；500 成功、501 退出 2（见 D-1） |
| TC-C 列出 | 默认只显示未完成；`--all` 含已完成；空清单 | 格式 `<id>. [ ] 内容` / `<id>. [x] 内容`；ID 升序；空清单 `暂无待办事项`，退出 0 |
| TC-D 完成 | 有效 ID；已完成项重复 `done`；`done 99` | 提示文案逐字一致；重复 `done` 退出 0 且 `done_at` 不被改写（可选强断言：数据内容不变）；不存在 → 退出 1 |
| TC-E 删除 | 有效 ID；删除后 `list --all` 无该项；删除后 `done/delete` 同 ID | 退出 1 且提示 `错误：找不到 ID 为 <id> 的待办` |
| TC-F ID 规则 | 添加 3 条 → 删除 #2 → 再添加 | 新 ID 为 4；`list --all` 无 #2 |
| TC-G 持久化 | 两个进程先后读写；`TODO_FILE` 生效；相对路径 `TODO_FILE`；`TODO_FILE=""` | 数据一致；`""` 时回退默认路径（测试需临时改 `HOME`，或仅断言不崩溃） |
| TC-H 数据文件 | 非法 JSON、空文件、纯空白文件、`todos` 非数组、ID 重复、`version=2`、非 UTF-8、路径是目录、只读文件 | 退出码与 stderr 文案；**记录并断言损坏文件内容字节级不变** |
| TC-I 参数用法 | `todo`、`todo badcmd`、`todo done`、`todo done abc`、`todo done -1`、`todo done 0`、`todo done 1.5`、`todo done １２３`、`todo list --all extra` | 退出 2；stderr 同时含帮助（`usage:`）与 `错误：…` |
| TC-J 帮助与退出码 | `todo --help`、`todo add --help`、`todo list --help` | 输出到 stdout、含用法与说明、退出 0 |
| TC-K 只读性 | `todo list` 在文件不存在时 | 退出 0 且**不创建文件** |

### 8.3 数据文件内容断言（golden 检查）

- `json.loads(Path.read_text(encoding="utf-8"))` 成功，且结构通过 5.2 的 JSON Schema。
- 顶层键集合恰好为 `{"version","next_id","todos"}`；每条待办键集合恰好为 5 个字段。
- 文件末尾有且仅有一个换行；中文以原字符存在（读原始文本，断言 `"买牛奶" in raw_text`，而非 `\u4e70`）。
- 时间字段正则：`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$`；完成后 `done_at` 非 null，ISO 字符串比较满足 `done_at >= created_at`。

### 8.4 不建议断言的实现细节

临时文件命名（`.todo-*.tmp`）、`indent` 之外的空白差异、错误消息中「错误：」之后的自由补充文案、内部函数名与日志。

### 8.5 测试风险清单（可能让用例假红）

| 风险 | 影响 | 规避 |
| --- | --- | --- |
| Windows/中文编码 | 输出乱码导致断言失败 | CI 设 `PYTHONIOENCODING=utf-8`；断言前 decode 已用 `text=True, encoding="utf-8"` |
| 以 root 运行 | 只读文件用例不触发 `PermissionError` | 跳过或标记为 `xfail` 当 `os.geteuid() == 0` |
| 时区/时钟跳变 | `done_at >= created_at` 失败 | 同一秒内完成时用 `>=`；不做秒级严格递增断言 |
| 并发用例 | 偶发丢更新 | 不设计并发用例，仅记录为已知限制 |
| 复用同一 `TODO_FILE` | 用例互相污染 | 每例独立 `tmp_path` |
| 依赖系统默认 HOME | 污染真实用户数据 | 一律显式设置 `TODO_FILE` |

---

## 9. 开发里程碑

| 里程碑 | 内容 | 完成判据 | 状态 |
| --- | --- | --- | --- |
| M1 骨架与 CLI 层 | 常量、`TodoError`、`build_parser`、自定义 `error()`、`--help` | `todo --help` 正常；`todo badcmd` 退出 2 且提示中文 | 已完成（`code/todo.py`） |
| M2 存储层 | `resolve_data_path`、`load_state`、`normalize_state`、`save_state`（原子写） | 不存在/损坏/空文件三类路径行为符合 5.4、6.2 | 已完成 |
| M3 业务命令 | `cmd_add` / `cmd_list` / `cmd_done` / `cmd_delete` + `validate_text` / `parse_todo_id` | PRD 6.2 / 6.3 示例逐条复现 | 已完成 |
| M4 加固与文档 | 退出码统一、`KeyboardInterrupt`/`BrokenPipe` 处理、`code/README.md` | `code/README.md` 可按步骤跑通全部示例 | 已完成 |
| M5 测试与验收 | 🧪 按第 8 节写测试计划与用例，跑通 AC-1 ~ AC-7 | 全绿（含边界用例）；测试报告归档到 `docs/` | 待测试同学 |
| M6 CEO 验收 | 确认 9 节 D-1 ~ D-5 决策项，冻结 v1 范围 | 决策记录写入本文件第 10 节 | 待 CEO |

依赖关系：M1 → M2 → M3 → M4 → M5 → M6，串行；M5 可与 M4 的文档修字并行。

---

## 10. 与 PRD 的差异 / 待 CEO 决策

| 编号 | 事项 | 本方案做法 | 需 CEO 决策 |
| --- | --- | --- | --- |
| N-1 | 文档命名 | PRD 写 `docs/DESIGN.md`，本文件为 `docs/TECH_DESIGN.md` | 认可即可，不回改 PRD |
| D-1 | 内容长度上限 | PRD 是「建议 500 字符」，本方案实现为**硬校验**（超长退出 2） | 保留硬校验 / 改为仅截断提示 / 完全取消上限 |
| D-2 | `add` 多词输入 | 用 `nargs="+"` 并空格拼接，`todo add 买 牛奶` 可用；引号包裹仍完全兼容 | 保留增强（更宽容）/ 改回严格要求引号 |
| D-3 | 空文件（0 字节） | 视为空清单，不报错 | 保留 / 改为报错退出 3 |
| D-4 | 数据文件权限 | 落盘为 `0600`（仅本人可读写） | 保留 / 改为跟随系统 umask（通常 0644） |
| D-5 | `list` 默认隐藏已完成 | 沿用 PRD 假设 A3，仅 `--all` 显示 | 保留 |
| D-6 | 重复 `done` 是否写盘 | 状态未变也重写一次文件（实现统一、幂等） | 保留 / 改为不必要不写盘 |

> 已按 PRD 假设 A1/A2/A4/A5 实现（命令名 `todo`、默认 `~/.todo.json` + `TODO_FILE`、删除无二次确认且 ID 不复用、重复完成不算错误）。以上决策项均不影响 AC-1 ~ AC-7 的验收，可在测试完成后统一确认。
