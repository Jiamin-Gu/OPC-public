# PRD：命令行待办工具 `todo`（v1）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 状态 | 待 CEO 确认（确认后即作为工程实现与测试的验收基线） |
| 作者 | 📋 产品经理 |
| 读者 | 💻 工程师（实现 + pytest）、CEO（验收） |
| 关联交付物 | `docs/PRD.md`（本文档）→ `code/`（实现）→ `tests/`（pytest 测试） |

---

## 1. 背景与目标

公司要做「一个简单好用的命令行待办工具」。v1 只解决一件事：**开发者在终端里能立刻记下待办、随时看到还剩什么没做、做完勾掉、不要的删掉，且数据留在本机不丢。**

v1 成功的判断标准（Definition of Done）：

1. 四个命令 `add` / `list` / `done` / `delete` 全部按本文档可用；
2. 数据持久化到本地 JSON 文件，进程退出后重开仍在；
3. `pytest` 全部通过，无网络依赖、不触碰用户真实数据文件（测试用临时文件）；
4. 无第三方运行时依赖（Python 标准库即可）。

**v1 明确的取舍**：宁可命令少、行为死板，也不要参数复杂、行为含糊。所有"更好用"的想法一律进 P1/P2 或第 12 节「不做」。

---

## 2. 目标用户与使用场景

### 2.1 目标用户

| 用户 | 特征 | 关键诉求 |
| --- | --- | --- |
| **主力用户：开发者 / 运维** | 长时间待在终端，习惯 `git`、`make` 这类"命令 + 子命令"体验；不想为了记一条待办切窗口打开 App | 记录要快（一条命令）、查看要一眼看懂、不干扰工作流（退出码可用于脚本串联） |
| **次要用户：终端重度用户** | 会用 SSH 登录服务器 / 容器；环境可能没图形界面 | 纯文本、无外部依赖、单文件存储、可被 `cat` 直接读 |
| **潜在用户：脚本 / CI** | 用 shell 脚本驱动 | 退出码语义清晰、输出可解析 |

非目标用户（v1 不服务）：需要共享任务、需要提醒/日历、需要看板视图的团队协作用户。

### 2.2 典型使用场景

- **S1（随手记）**：Alice 在终端调试时想到"要加个索引"，直接 `todo add 给 orders 表加索引`，2 秒后继续工作。
- **S2（开工先看）**：Bob 每天打开终端第一件事 `todo list`，只看未完成项，判断今天做什么。
- **S3（完成后勾掉）**：Alice 做完一件事，`todo done 3`，再 `todo list` 确认这项不在未完成列表里。
- **S4（清理垃圾）**：Bob 发现某条待办写错了/不做了，`todo delete 5` 删掉。
- **S5（历史回看）**：Bob 想知道自己这周做完了什么，`todo list --all`，通过 `[ ]` / `[x]` 一眼区分状态。
- **S6（多环境隔离）**：Bob 在服务器上想用独立文件，`export TODO_FILE=/srv/ops/todos.json`，与笔记本上的待办互不干扰。

---

## 3. 要解决的问题

| # | 问题 | v1 的解法 |
| --- | --- | --- |
| P-1 | 记待办的成本高于"记在脑子里"，于是根本不会记 | `todo add <文本>` 一条命令搞定，无需引号（多词自动合并），无交互确认 |
| P-2 | 待办散落在便签/编辑器/Slack 里，看不到全局 | 单一 JSON 文件作为唯一数据源，`todo list` 一条命令看全 |
| P-3 | 无法区分"做完了"和"没做完" | `done` 字段 + `[ ]` / `[x]` 状态标识 + `list` 默认只显示未完成 |
| P-4 | 记错的任务堆积，列表越长越不想看 | `delete <id>` 一条命令移除，ID 稳定不复用，不会误删别人的项 |
| P-5 | 工具重启后数据丢失 | 每次写操作立即落盘到 JSON，进程退出后数据仍在 |
| P-6 | 想用脚本处理待办，但工具退出码混乱 | 明确退出码契约：0 成功 / 1 业务错误 / 2 用法错误 |

---

## 4. 范围与优先级

### 4.1 命令总览

CLI 名称：`todo`（安装后可执行入口，Python 包建议名 `todo_cli`，避免与 PyPI 同名包冲突）。

```
todo [--file PATH] [--version] [-h] <command> [args]
```

| 命令 | 语法 | 优先级 | 说明 |
| --- | --- | --- | --- |
| `add` | `todo add TEXT [TEXT...]` | **P0** | 添加一条待办 |
| `list` | `todo list [--all]` | **P0** | 列出待办（默认只看未完成；`--all` 看全部） |
| `done` | `todo done ID` | **P0** | 把某条待办标记为已完成 |
| `delete` | `todo delete ID` | **P0** | 删除某条待办 |
| 全局 | `--file PATH` / `TODO_FILE` 环境变量 | **P0** | 指定数据文件路径 |
| 全局 | `--version` | **P1** | 打印版本号 |
| `list --done` | `todo list --done` | **P1** | 只看已完成项 |
| 视觉 | 状态标识 `[ ]` / `[x]` | **P0** | 见下方说明 |
| 视觉 | 仅在 TTY 下使用颜色 | **P1** | 非 TTY（管道/重定向）必须输出纯文本 |

> 关于「状态标识」的优先级说明：任务书把「清晰的状态标识」列为 P1，但 P0 的 `list --all` 若不区分状态则该功能没有意义，因此 **`[ ]` / `[x]` 文字标识归 P0**（纯文本、无颜色，保证管道可用），颜色高亮等增强归 P1。

### 4.2 P0（v1 必须完成，缺一不可）

1. `todo add`：添加待办，分配单调递增且不复用的 ID，立即落盘；
2. `todo list`：默认只列未完成项；`--all` 列全部并带状态标识；
3. `todo done`：标记完成，幂等；
4. `todo delete`：删除指定 ID 的待办；
5. 数据持久化到本地 JSON 文件，路径解析顺序：`--file PATH` > 环境变量 `TODO_FILE` > 默认 `~/.todo.json`；
6. 空列表、ID 不存在、文件损坏等边界情况有明确输出与退出码（见第 6、8 节）；
7. pytest 测试覆盖第 11 节清单，全部通过。

### 4.3 P1（P0 完成后做，允许推迟到 v1.1）

- `list --done`：只列已完成项；
- `todo --version` 输出版本号；
- TTY 下颜色区分状态（非 TTY 自动关闭；`--no-color` 可选）；
- 持续写入时用 0600 权限创建文件（隐私：待办内容不外泄给同机其他用户）；
- `list` 显示时按终端宽度截断超长文本（存储不截断），截断处加 `…`；
- `done` 写入 `completed_at` 时间戳（可空字段，见 6.3）。

### 4.4 P2（v1 不做，仅登记）

`todo edit <id>` 改文本；`todo done/delete` 支持多个 ID（`todo done 1 2 3`）；`list --sort`、`--limit`；`todo clean`（批量清除已完成）；`--quiet` / `--json` 机器可读输出；shell 补全；交互式 TUI。

---

## 5. 术语与全局约定（工程契约）

| 约定项 | 规定 |
| --- | --- |
| 语言/运行时 | Python **3.9+**，P0 只用标准库（`argparse`、`json`、`pathlib`、`os`、`tempfile`、`datetime`） |
| 命令与参数名 | 全小写英文；长选项用 `--all`，短选项 `-a`（P1 起） |
| **输出文案语言** | 命令输出（成功提示、错误提示、帮助）**统一用英文**，避免编码/本地化歧义；本文档给出的英文句子即为文案契约。**待办文本本身**支持任意 UTF-8（中文、emoji），按原样存储与显示 |
| 标准输出 stdout | 只放正常业务输出（成功提示、列表内容、帮助） |
| 标准错误 stderr | 只放错误信息，格式统一为 `Error: <说明>` |
| 退出码 | `0` 成功；`1` 运行时/业务错误（ID 不存在、文件读/写失败、文件格式非法）；`2` 用法错误（未知子命令、缺少必需参数、文本为空、ID 不是正整数） |
| ID 规则 | 正整数，从 1 开始单调递增，**永不回收**（删除 #2 后下一条新待办是 #3，不是 #2）。同一次会话内 ID 稳定 |
| 排序规则 | 所有输出按 `id` 升序（即创建顺序） |
| 时间格式 | UTC，ISO 8601，秒级精度，带 `Z` 后缀，例：`2025-05-01T02:10:33Z`。v1 不在输出中展示时间 |
| 文本清洗 | 输入文本先 `strip()`；内部换行与连续空白折叠为单个空格；清洗后为空 → 用法错误（退出码 2） |
| 无参数调用 | `todo`（无子命令）→ 打印简短用法到 stderr，退出码 2；`todo -h` / `todo --help` → 帮助到 stdout，退出码 0 |
| 可测试性要求 | `cli.main(argv: list[str] \| None = None) -> int` 必须存在且**返回退出码而非直接 `sys.exit`**，便于 pytest 直接调用；`if __name__ == "__main__"` 与 console script 才调用 `sys.exit(main())` |
| 帮助文本 | 每个子命令 `todo <cmd> -h` 必须有一句说明 + 参数说明 + 至少 1 个示例 |

---

## 6. 数据模型与存储

### 6.1 文件路径解析（优先级从高到低）

1. 命令行全局选项 `--file PATH`（必须写在子命令之前：`todo --file /tmp/t.json add 写周报`）；
2. 环境变量 `TODO_FILE`（值为路径字符串，支持 `~` 展开）；
3. 默认值：**`~/.todo.json`**（即 `Path.home() / ".todo.json"`）。

> 已决策：默认用 `~/.todo.json` 而不是当前目录 `todos.json`。理由：待办是"人的数据"而非"项目的数据"，跟着 cwd 走会导致在 `~/work/a` 记的待办在 `~/work/b` 看不到（违反 S2 场景）；且避免把 JSON 提交进 git 仓库。
> `--file` 与 `TODO_FILE` 同时提供时，以 `--file` 为准（便于测试完全掌控路径）。

### 6.2 JSON 文件结构（v1 契约，版本号 1）

```json
{
  "version": 1,
  "next_id": 3,
  "todos": [
    { "id": 1, "text": "买牛奶", "done": false, "created_at": "2025-05-01T02:10:33Z" },
    { "id": 2, "text": "写周报", "done": true,  "created_at": "2025-05-01T02:11:05Z" }
  ]
}
```

| 字段 | 类型 | 必填 | 定义与约束 |
| --- | --- | --- | --- |
| `version` | int | 是 | 结构版本，v1 固定为 `1`。读到 ≠ 1 → 视为不支持，报错退出码 1，**不得覆盖原文件** |
| `next_id` | int | 是 | 下一条待办的 ID，≥ 1。添加后自增；删除不回收 → ID 不复用 |
| `todos` | array | 是 | 待办数组，可为空数组 `[]`；写入时按 `id` 升序 |
| `todos[].id` | int | 是 | 正整数，文件内唯一 |
| `todos[].text` | string | 是 | 已清洗的非空字符串，UTF-8 |
| `todos[].done` | bool | 是 | `false` 未完成 / `true` 已完成 |
| `todos[].created_at` | string | 是 | 创建时间，UTC ISO 8601 秒精度带 `Z` |
| `todos[].completed_at` | string \| null | 否（**P1**） | 完成时间，格式同上；未完成时为 `null` 或字段缺失 |

文件示例（空文件）：

```json
{ "version": 1, "next_id": 1, "todos": [] }
```

工程约束：

- **未知字段必须原样保留**。实现应把每条 todo 当 dict 处理（读出 → 改字段 → 写回），不要用固定 schema 重建后丢弃未知键，为将来版本升级留出兼容空间。
- 文件编码 UTF-8；写出时 `ensure_ascii=False` 且 2 空格缩进，保证用户 `cat` 可读。

### 6.3 读写规则（工程必须遵守）

| 规则 | 规定 |
| --- | --- |
| R-1 文件不存在 | 视为「空列表」（`next_id=1, todos=[]`），**不是错误**。只读命令（`list`）**不得创建文件**；首个写命令（`add`）才创建 |
| R-2 读失败（权限等） | stderr 输出 `Error: cannot read todo file <abs_path>: <原因>`，退出码 1 |
| R-3 解析失败 | JSON 语法错误 / 顶层不是对象 / 字段类型不符 / `version` 不支持 → stderr 输出 `Error: invalid todo file <abs_path>: <原因>`，退出码 1，**绝不改写或删除原文件** |
| R-4 原子写 | 写文件必须"先写同目录临时文件 → `os.replace()` 覆盖"，保证进程中途被杀不会留下半截 JSON；写失败时原文件内容保持不变 |
| R-5 目录不存在 | 不自动递归创建目录（例如 `--file /tmp/a/b/t.json` 且 `/tmp/a` 不存在）→ `Error: cannot write todo file <abs_path>: 目录不存在`，退出码 1 |
| R-6 只读命令不写入 | `list` 不得改变文件内容、mtime（测试会断言这点） |
| R-7 ID 分配 | `new_id = next_id`，随后 `next_id += 1`，二者在同一次原子写中落盘 |
| R-8 CRUD 落盘时机 | `add` / `done` / `delete` 在返回 0 之前必须已完成落盘 |
| R-9 并发 | v1 **不做**文件锁。并发写可能互相覆盖，属已知限制（见第 12 节），文档中不做承诺 |

### 6.4 建议的代码结构（供工程师参考，非强制）

```
code/
  pyproject.toml          # 项目元数据 + console_scripts: todo = "todo_cli.cli:main"
  todo_cli/__init__.py    # __version__ = "0.1.0"
  todo_cli/models.py      # Todo 的 dict 级读写辅助（create/mark_done/...）
  todo_cli/storage.py     # 路径解析、load()、save()、原子写、错误类型
  todo_cli/cli.py         # argparse 定义 + 各命令处理 + main(argv) -> int
tests/
  test_cli.py             # 见第 11 节
```

---

## 7. 输出格式规范（给设计师 / 文案，也是测试断言依据）

### 7.1 `list` 行格式

```
<id>. [<状态标识>] <text>
```

- `<id>`：右对齐无要求，直接十进制数字，后跟 `. `；
- `<状态标识>`：未完成 `[ ]`，已完成 `[x]`（**半角方括号 + 一个空格**，小写 x）；
- `<text>`：原文，不额外加引号；
- 每行以 `\n` 结尾，最后一行也有换行；
- 无表头、无缩进、无分页。

```
1. [ ] 买牛奶
2. [x] 写周报
3. [ ] 给 orders 表加索引
```

### 7.2 固定文案表（英文，逐字对照）

| 场景 | 流 | 文案 |
| --- | --- | --- |
| add 成功 | stdout | `Added #<id>: <text>` |
| list 无未完成项 | stdout | `No pending todos.` |
| list --all 无任何待办 | stdout | `No todos yet.` |
| list --done 无已完成项（P1） | stdout | `No completed todos.` |
| done 成功 | stdout | `Completed #<id>: <text>` |
| done 已是完成状态 | stdout | `Todo #<id> is already completed.` |
| delete 成功 | stdout | `Deleted #<id>: <text>` |
| ID 不存在 | stderr | `Error: todo #<id> not found.` |
| 文本为空 | stderr | `Error: todo text cannot be empty.` |
| ID 非法 | stderr | `Error: '<输入值>' is not a valid todo id.` |
| 读文件失败 | stderr | `Error: cannot read todo file <abs_path>: <原因>` |
| 文件和格式非法 | stderr | `Error: invalid todo file <abs_path>: <原因>` |
| 写文件失败 | stderr | `Error: cannot write todo file <abs_path>: <原因>` |
| 未知子命令 | stderr | argparse 用法错误（退出码 2），须含 `invalid choice` |

> 文案契约说明：验收以**退出码 + 上述句子为准**。ID 不存在与文本为空的提示必须逐字一致；argparse 自动生成的用法错误只需保证退出码为 2 且 stderr 含关键词。

---

## 8. 命令详细规格（用户故事 + 验收标准）

通用前提：以下每条 AC 都可独立执行；`<文件>` 指本次使用 `--file <tmp>` 或 `TODO_FILE` 指定的临时文件。

### 8.1 `todo add` — 添加待办（P0）

**用户故事**
> 作为在终端里工作的开发者，我想用一条命令把脑中的待办写下来，这样我不用切窗口、不用引号、也不用确认，就能继续手头的工作。

**语法**
```
todo [--file PATH] add TEXT [TEXT...]
```
**参数**
- `TEXT [TEXT...]`：必填，至少 1 个位置参数；多个参数用**单个空格**连接成一条文本（因此 `todo add 给 orders 表加索引` 与 `todo add "给 orders 表加索引"` 等价）。

**示例**
```
$ todo add 买牛奶
Added #1: 买牛奶

$ todo add "写周报"
Added #2: 写周报

$ todo add 给 orders 表加索引
Added #3: 给 orders 表加索引
```

**验收标准**

| 编号 | Given | When | Then |
| --- | --- | --- | --- |
| AC-ADD-1 | 文件不存在 | `todo add 买牛奶` | stdout 逐字 `Added #1: 买牛奶`；退出码 0；文件被创建，内容为 `{"version":1,"next_id":2,"todos":[{"id":1,"text":"买牛奶","done":false,"created_at":"<UTC ISO8601Z>"}]}` |
| AC-ADD-2 | 已有 id=1 | `todo add 写周报` | stdout `Added #2: 写周报`；退出码 0；`next_id` 变为 3；原有条目字段保持不变 |
| AC-ADD-3 | 任意 | `todo add 给 orders 表加索引` | 存储文本为 `给 orders 表加索引`（未加引号的多词参数按空格连接，不丢失词间空格） |
| AC-ADD-4 | 任意 | `todo add "  多余 空格  "` | 存储文本为 `多余 空格`（首尾 strip、内部连续空白折叠为 1 个空格） |
| AC-ADD-5 | 任意 | `todo add $'a\nb'` | 存储文本为 `a b`（换行按空白折叠） |
| AC-ADD-6 | 任意 | `todo add ""` | stderr 含 `Error: todo text cannot be empty.`；退出码 2；**文件未被创建/未被修改** |
| AC-ADD-7 | 任意 | `todo add` | 退出码 2（缺必需参数），stderr 含 `usage` |
| AC-ADD-8 | 已有 3 条待办 | `todo add 新任务` | 新条目 id 为 4（不因前面有已完成项而改变规则） |
| AC-ADD-9 | 任意 | `todo add 🌱 浇水` | 文件与后续 `list` 输出中均正确显示 emoji（UTF-8 往返一致） |

### 8.2 `todo list` — 列出待办（P0）

**用户故事**
> 作为终端用户，我想要一条命令看到"我还剩哪些没做"，并且在需要复盘时用 `--all` 看到全部项及完成状态，这样我一眼就知道接下来该做什么。

**语法**
```
todo [--file PATH] list [--all | -a]
```
**参数**
- `--all` / `-a`：可选。省略 → 只显示 `done == false` 的项；提供 → 显示全部项（未完成 + 已完成）。

**示例**
```
$ todo list
1. [ ] 买牛奶
3. [ ] 给 orders 表加索引

$ todo list --all
1. [ ] 买牛奶
2. [x] 写周报
3. [ ] 给 orders 表加索引

$ todo list
No pending todos.

$ todo list --all
No todos yet.
```

**验收标准**

| 编号 | Given | When | Then |
| --- | --- | --- | --- |
| AC-LIST-1 | 文件不存在 | `todo list` | stdout 逐字 `No pending todos.`；退出码 0；**文件未被创建** |
| AC-LIST-2 | `todos: []` | `todo list --all` | stdout 逐字 `No todos yet.`；退出码 0 |
| AC-LIST-3 | id1 未完成、id2 已完成、id3 未完成 | `todo list` | stdout 恰为两行，按 id 升序：`1. [ ] 买牛奶\n3. [ ] 给 orders 表加索引\n`；**不含 id2 所在行**；退出码 0 |
| AC-LIST-4 | 同上 | `todo list --all` | 三行，按 id 升序，状态标识分别为 `[ ]`、`[x]`、`[ ]`；退出码 0 |
| AC-LIST-5 | 全部已完成 ≥1 条 | `todo list` | stdout 逐字 `No pending todos.`；退出码 0（空视图不算错误） |
| AC-LIST-6 | 任意 | `todo list` 后 | 文件内容与 mtime 不变（只读命令不写盘） |
| AC-LIST-7 | 任意 | `todo list --all -a` 之前/之后各自执行 | `-a` 与 `--all` 行为完全一致 |
| AC-LIST-8 | 文本含中文/emoji | `todo list` | 输出按 7.1 格式逐字匹配，行尾有换行 |
| AC-LIST-9 | 任意 | `todo list -h` | 退出码 0，stdout 含命令说明与 `--all` 说明 |

### 8.3 `todo done` — 标记完成（P0）

**用户故事**
> 作为终端用户，我做完一件事后想立刻把它标记为完成，这样我的未完成列表才始终可信；而且我不希望"重复标记已完成的项"变成一个报错打断我。

**语法**
```
todo [--file PATH] done ID
```
**参数**
- `ID`：必填，正整数，对应 `todos[].id`。v1 只接受单个 ID（多个 ID 属 P2）。

**示例**
```
$ todo done 1
Completed #1: 买牛奶

$ todo done 1
Todo #1 is already completed.

$ todo done 99
Error: todo #99 not found.
$ echo $?
1
```

**验收标准**

| 编号 | Given | When | Then |
| --- | --- | --- | --- |
| AC-DONE-1 | id1 未完成 | `todo done 1` | stdout 逐字 `Completed #1: 买牛奶`；退出码 0；文件中该条 `done == true`，其他条目不变，`next_id` 不变 |
| AC-DONE-2 | id1 已完成 | `todo done 1` | stdout 逐字 `Todo #1 is already completed.`；退出码 0（幂等成功）；文件内容与执行前完全一致（不重复写盘） |
| AC-DONE-3 | 无 id=99 | `todo done 99` | stderr 逐字 `Error: todo #99 not found.`；退出码 1；stdout 为空；文件内容不变 |
| AC-DONE-4 | 任意 | `todo done abc` | 退出码 2；stderr 含 `Error: 'abc' is not a valid todo id.`；文件内容不变 |
| AC-DONE-5 | 任意 | `todo done 0` 或 `todo done -1` | 退出码 2（ID 必须为正整数）；文件内容不变 |
| AC-DONE-6 | 文件是空文件 / `todos: []` | `todo done 1` | stderr `Error: todo #1 not found.`；退出码 1 |
| AC-DONE-7 | 删除 id2 后 id1、id3 存在 | `todo done 2` | stderr `Error: todo #2 not found.`；退出码 1（已删除的 ID 不再存在，也不会被复用） |
| AC-DONE-8 | id1 未完成 | `todo done 1` 后 `todo list` | 输出中不再出现 id1（默认视图只列未完成）；`todo list --all` 中显示 `1. [x] 买牛奶` |

### 8.4 `todo delete` — 删除待办（P0）

**用户故事**
> 作为终端用户，我想把记错的、不再需要的待办彻底删掉，这样列表不会越积越长；删除不该影响其他项的编号，也不该有交互式确认（否则没法在脚本里用）。

**语法**
```
todo [--file PATH] delete ID
```
**参数**
- `ID`：必填，正整数。**无二次确认**（v1 决策：非交互优先，交互确认会让脚本不可用；误删风险由"ID 不复用 + 无 `--all` 批量删除"来限制）。

**示例**
```
$ todo delete 2
Deleted #2: 写周报

$ todo delete 2
Error: todo #2 not found.

$ todo delete abc
Error: 'abc' is not a valid todo id.
```

**验收标准**

| 编号 | Given | When | Then |
| --- | --- | --- | --- |
| AC-DEL-1 | id2 存在 | `todo delete 2` | stdout 逐字 `Deleted #2: 写周报`；退出码 0；文件中不再有 id2；其他条目与 `next_id` 不变 |
| AC-DEL-2 | id2 已删除 | 再次 `todo delete 2` | stderr 逐字 `Error: todo #2 not found.`；退出码 1；文件内容不变 |
| AC-DEL-3 | 已删除 id2（`next_id` 仍为 4） | `todo add 新任务` | 新条目 id 为 4，**不是 2**（ID 不复用） |
| AC-DEL-4 | 仅剩 id1 | `todo delete 1` | 退出码 0；文件变为 `todos: []`，`next_id` 保持单调递增的值 |
| AC-DEL-5 | 任意 | `todo delete abc` / `todo delete 0` | 退出码 2；stderr 含 `not a valid todo id`；文件内容不变 |
| AC-DEL-6 | 文件不存在 | `todo delete 1` | stderr `Error: todo #1 not found.`；退出码 1；**不创建文件** |
| AC-DEL-7 | 任意 | `todo delete 1` | 无任何交互提示/等待输入（测试可断言执行不阻塞） |

### 8.5 全局选项与用法错误

**用户故事**
> 作为需要在服务器或多环境使用工具的开发者，我想指定自己的数据文件；作为脚本作者，我希望用法写错时拿到明确的非 0 退出码。

**语法**
```
todo [--file PATH] [--version] [-h|--help] <command> [args]
```

| 编号 | Given | When | Then |
| --- | --- | --- | --- |
| AC-G-1 | 任意 | `todo --file /tmp/x/todos.json add 测试` | 数据写入 `/tmp/x/todos.json`（而非 `~/.todo.json`）；其余行为同 AC-ADD-1 |
| AC-G-2 | `TODO_FILE=/tmp/y.json` | `todo add a` | 写入 `/tmp/y.json` |
| AC-G-3 | `TODO_FILE=/tmp/y.json` | `todo --file /tmp/x.json add a` | 写入 `/tmp/x.json`（`--file` 优先于环境变量） |
| AC-G-4 | 未设置 `TODO_FILE` | `todo add a` 后 `ls ~/.todo.json` | 存在 `~/.todo.json`（默认路径生效） |
| AC-G-5 | 任意 | `todo` | 退出码 2；stderr 含用法说明；stdout 为空 |
| AC-G-6 | 任意 | `todo frobnicate` | 退出码 2；stderr 含 `invalid choice` |
| AC-G-7 | 任意 | `todo --help` | 退出码 0；stdout 列出 4 个子命令名与全局选项 |
| AC-G-8 | 默认路径含 `~` | `todo --file '~/t.json' add a` | 正确展开为 `$HOME/t.json`（写入家目录下文件） |
| AC-G-9（**P1**） | 任意 | `todo --version` | stdout `todo 0.1.0`（版本号与 `todo_cli.__version__` 一致）；退出码 0 |

### 8.6 文件异常场景

| 编号 | Given | When | Then |
| --- | --- | --- | --- |
| AC-ERR-1 | 文件内容为 `{ 这不是 json` | `todo list` | stderr 含 `Error: invalid todo file` 与文件路径；退出码 1；**文件内容逐字节不变** |
| AC-ERR-2 | 文件内容为 `{"version":2,"next_id":1,"todos":[]}` | `todo add a` | stderr 含 `Error: invalid todo file`（版本不支持）；退出码 1；**文件内容逐字节不变**（不降级、不清空） |
| AC-ERR-3 | 文件内容为 `{"todos":"oops"}` | `todo list` | 退出码 1；stderr 含 `Error: invalid todo file`；文件不变 |
| AC-ERR-4 | 文件存储为只读（`chmod 444`） | `todo add a` | 退出码 1；stderr 含 `Error: cannot write todo file`；原文件内容不变 |
| AC-ERR-5 | `--file /tmp/nonexist_dir_a/b/t.json` 且目录不存在 | `todo add a` | 退出码 1；stderr 含 `Error: cannot write todo file`；不创建目录 |
| AC-ERR-6 | 文件存在且有 2 条待办 | 任意写命令 | 写入完成后文件仍是合法 JSON，`version=1`，未知字段（如手工添加的 `"tag": "x"`）被保留 |
| AC-ERR-7 | 任意 | 写命令执行期间被 `kill -9`（可选手工验证） | 文件要么是旧内容、要么是新内容，不存在半截 JSON（原子写） |

---

## 9. 非功能需求

| 类别 | 要求 |
| --- | --- |
| 性能 | 本机、1000 条待办以内，任一命令端到端 < 200 ms；启动不加载任何非必要模块 |
| 依赖 | P0 仅 Python 标准库；测试仅需 `pytest`（开发依赖） |
| 平台 | Linux / macOS 必须通过；Windows 不做强制保证，但实现不得使用 Unix-only API（用 `pathlib`、`os.replace`） |
| 编码 | 文件 UTF-8；终端输出 UTF-8；支持中文与 emoji |
| 安全 | 无网络访问；不读取/上传待办内容；不执行待办文本中的任何内容（纯数据） |
| 数据安全 | 原子写；错误场景绝不破坏原文件；测试绝不触碰 `~/.todo.json` |
| 可用性 | 无需任何配置即可跑起来（默认路径开箱可用）；`--help` 与每个子命令 `-h` 均可用 |

---

## 10. 里程碑

| 阶段 | 产出 | 负责 |
| --- | --- | --- |
| M1 | 本 PRD 定稿（CEO 确认） | 📋 产品经理 |
| M2 | `code/` 实现 P0 全部功能，`todo` 可执行 | 💻 工程师 |
| M3 | `tests/` pytest 全绿；覆盖 AC-ADD/LIST/DONE/DEL/G/ERR 全部 P0 编号 | 💻 工程师 |
| M4 | CEO 按第 8 节示例逐条手工验收 | CEO |

---

## 11. 测试要求（给工程师，pytest）

**测试隔离硬要求**

```python
def test_add_creates_file(tmp_path, monkeypatch):
    todo_file = tmp_path / "todos.json"
    monkeypatch.setenv("TODO_FILE", str(todo_file))
    rc = cli.main(["add", "买牛奶"])
    assert rc == 0
    ...
```
- 一律通过 `monkeypatch.setenv("TODO_FILE", ...)` 或 `["--file", str(tmp_path / "todos.json"), ...]` 指定临时文件；
- 必须有断言/夹具防护：任何测试都不得读写真实 `~/.todo.json`（可加一条测试或 fixture 校验 `TODO_FILE` 已设置）；
- 优先直接调用 `cli.main(argv) -> int`（快、可断言返回值），并**至少 1 条**通过 `subprocess` 跑 `python -m todo_cli ...` 或 console script 的端到端测试，验证退出码与真实进程行为一致；
- 断言方式：退出码 + stdout/stderr 内容（建议逐字断言第 7.2 节文案；argparse 相关只断言关键子串）。

**必须覆盖的用例（对应 AC 编号，至少这些）**

1. `add`：AC-ADD-1/2/3/4/5/6/7/8/9（含空文本不写文件、多词连接、空白折叠、emoji 往返）
2. `list`：AC-LIST-1/2/3/4/5/6/7/8/9（含只读不写盘、空视图、状态标识）
3. `done`：AC-DONE-1/2/3/4/5/6/7/8（含幂等、非法 ID、已删除 ID、默认视图联动）
4. `delete`：AC-DEL-1/2/3/4/5/6/7（含 ID 不复用、不创建文件）
5. 全局：AC-G-1/2/3/4/5/6/7/8（`~` 展开可用 `tmp_path` + monkeypatch HOME 验证）
6. 文件异常：AC-ERR-1/2/3/4/6（含"原文件逐字节不变"断言）；AC-ERR-5 至少覆盖退出码与 stderr 关键词
7. 数据完整性：连续 `add`→`done`→`delete`→`add` 后，读回 JSON 断言 `version`、`id` 单调性、`next_id` 正确、`created_at` 匹配 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`

**完成定义（DoD）**：`pytest -q` 全部通过；无 skipped（除需特权/平台条件的用例，且需注释说明）；测试运行期间不产生 `~/.todo.json`。

---

## 12. 明确不做的范围（Out of Scope，v1）

| # | 不做 | 原因 |
| --- | --- | --- |
| 1 | GUI / Web / TUI 界面 | v1 只服务终端用户，界面会淹没"简单"这一核心价值 |
| 2 | 多用户、账号、云同步、共享清单、团队协作 | 单机单文件即可解决 P-1~P-6；同步涉及冲突解决，成本远超 v1 收益 |
| 3 | 优先级、标签、分类、项目/清单分组 | 需要交互式 UI 或更多参数，v1 用"ID + 文本"足够 |
| 4 | 截止日期、提醒、重复任务、日历集成 | 提醒需要常驻进程/系统集成，与"简单 CLI"冲突 |
| 5 | 配置文件（如 `~/.todorc`）、别名、默认选项持久化 | CEO 明确不做；v1 仅支持 `--file` 与 `TODO_FILE`（不落配置文件） |
| 6 | 修改待办文本（`edit`） | P2；v1 用 `delete` + `add` 替代 |
| 7 | 批量操作（多 ID 的 `done`/`delete`、`clean` 清空已完成） | 批量删除风险高、参数解析复杂，v1 单 ID |
| 8 | 排序 / 过滤扩展（按时间、关键字搜索、`--limit`） | 待办数量在 v1 量级很小，YAGNI |
| 9 | 回收站 / 撤销 / 归档 | 需要额外状态与命令，"删了就是删了"更符合 v1 的简单定位 |
| 10 | 导入导出（CSV / Markdown / iCal）、与 issue tracker 集成 | 无明确需求来源 |
| 11 | 加密 / 密码保护 / 敏感信息处理 | 用户自定义文件路径 + （P1）0600 权限已够 |
| 12 | 文件锁、多进程并发安全、跨设备同步冲突处理 | 明确为已知限制：并发写可能互相覆盖；v1 假设单人单机顺序使用 |
| 13 | 命令输出多语言（i18n）、本地化 | v1 输出固定英文，减少文案与测试成本 |
| 14 | 自动更新、遥测、崩溃上报 | 不做任何网络行为 |
| 15 | 时间/耗时统计报表（"本周完成 5 条"） | 属统计功能，v1 只存 `created_at` 备用 |
| 16 | 与 shell 深度集成（补全脚本、statusline 插件） | P2 以后 |

---

## 13. 假设与待 CEO 决策

**本次做出的假设（如与预期不符请指出，会同步改文档与实现）**

| # | 假设 | 说明 |
| --- | --- | --- |
| A-1 | 默认数据文件为 `~/.todo.json`，可用 `--file` / `TODO_FILE` 覆盖 | 理由见 6.1；若希望默认写在当前目录的 `todos.json`，只需改 6.1 与 AC-G-4 |
| A-2 | 命令输出文案统一英文，待办文本原样支持中文/emoji | 见第 5 节 |
| A-3 | CLI 可执行名为 `todo`，Python 包名 `todo_cli`，初始版本号 `0.1.0` | 避免包名与 PyPI 现有包冲突 |
| A-4 | 退出码为 0 / 1 / 2 三档语义 | 见第 5 节 |
| A-5 | ID 在删除后不复用（用 `next_id` 持久化） | 避免"删了 #2、新建又变 #2"的误操作 |
| A-6 | `delete` 无二次确认 | 见 8.4 |
| A-7 | 顶层 JSON 用对象（含 `version`/`next_id`/`todos`）而非裸数组 | 便于版本迁移与 ID 管理 |

**待决策（有默认答案，不阻塞开工）**

1. 是否保留 `todo` 作为可执行名，还是要求带前缀（如 `td`）以免与系统已有命令冲突？
2. P1 是否要在 v1 一起交付（尤其 `list --done`、`--version`、颜色）？本 PRD 默认 v1 只交 P0。
3. 是否需要 `todo edit <id>` 进 P1（当前为 P2）？
4. 是否需要"待办文本长度上限"（当前不限；仅 P1 显示截断）？

---

## 14. 附：v1 手工验收脚本（CEO 用）

```bash
export TODO_FILE="$(mktemp -d)/todos.json"

todo list                          # 期望：No pending todos.  ($? = 0)
todo add 买牛奶                     # 期望：Added #1: 买牛奶
todo add 写周报                     # 期望：Added #2: 写周报
todo list                          # 期望：1. [ ] 买牛奶  /  2. [ ] 写周报
todo done 2                        # 期望：Completed #2: 写周报
todo list                          # 期望：只有 1. [ ] 买牛奶
todo list --all                    # 期望：1. [ ] 买牛奶  /  2. [x] 写周报
todo done 2                        # 期望：Todo #2 is already completed.  ($? = 0)
todo done 99; echo $?              # 期望：Error: todo #99 not found.  / 1
todo delete 2                      # 期望：Deleted #2: 写周报
todo add 新任务                     # 期望：Added #3: 新任务   ← ID 不复用
todo delete abc; echo $?           # 期望：Error: 'abc' is not a valid todo id.  / 2
todo add ""; echo $?               # 期望：Error: todo text cannot be empty.  / 2
cat "$TODO_FILE"                   # 期望：合法 JSON，version=1，中文可读
pytest -q                          # 期望：全部通过
```
