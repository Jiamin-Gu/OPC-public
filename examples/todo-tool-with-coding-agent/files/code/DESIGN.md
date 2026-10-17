# 技术方案：命令行待办工具 `todo` v1（P0）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 作者 | 💻 工程师 |
| 依据 | `docs/PRD.md` v1.0（第 5/6/7/8/11 节为硬契约） |
| 交付物 | `code/todo_cli/`（实现）、`code/tests/`（pytest）、`code/README.md`（运行说明） |
| 状态 | 与 PRD 一致；差异见第 8 节 |

---

## 1. 架构分层

分三层，依赖单向（上层依赖下层，下层不反向依赖），保证 `storage` 可被独立测试、`cli` 只做参数解析与文案输出。

```
┌───────────────────────────────────────────────┐
│ 表示层  cli.py                                │
│  argparse 定义、命令分发、文案输出、退出码映射  │
│  main(argv: list[str] | None) -> int          │
└───────────────┬───────────────────────────────┘
                │ 只调用 store 的公开方法，不直接 open()/json.load()
┌───────────────▼───────────────────────────────┐
│ 业务层  models.py                             │
│  TodoStore：内存中的待办集合 + next_id         │
│  add / list_pending / list_all / mark_done /  │
│  delete ；纯逻辑，不碰文件、不打印            │
└───────────────┬───────────────────────────────┘
                │ 传入/返回 plain dict（保留未知字段）
┌───────────────▼───────────────────────────────┐
│ 存储层  storage.py                            │
│  resolve_path() / load() / save()（原子写）    │
│  结构校验、错误类型定义 errors.py             │
└───────────────────────────────────────────────┘
```

为什么这样分：

- **可测试性**：PRD 第 11 节要求直接调用 `cli.main(argv) -> int`。三层分离后，AC-ERR-1/2/3（损坏文件）只需构造一个文件再调 `cli.main`，无需 mock 任何东西；同时 `models` 的 ID 分配规则（不复用）可以单独做纯函数测试。
- **未知字段保留**（PRD 6.2 工程约束）：`TodoStore` 内部把每条 todo 当 `dict` 处理，"读出 → 改字段 → 写回"，而不是反序列化成固定 dataclass 后重新构造。`dataclass` 会天然丢键，因此业务层只对**已明确契约的字段**做类型校验（`id`/`text`/`done`/`created_at`），其余键原样透传。
- **文案与逻辑分离**：所有固定文案集中放在 `cli.py` 顶部常量区，与 PRD 7.2 表格逐条对应，便于对照验收与改动。

## 2. 技术选型及理由

| 选型 | 决定 | 理由（对照 PRD 约束） |
| --- | --- | --- |
| 语言/运行时 | Python 3.9+ | PRD 5 节硬约束。代码不使用 3.10+ 语法（避免 `X \| Y` 类型注解、`match`），仅在 docstring 中写联合类型 |
| 参数解析 | 标准库 `argparse` + 子解析器（`add_subparsers`） | PRD 4.1 语法即"命令 + 子命令"；argparse 自动给出 `usage`、`invalid choice`（AC-G-5/6 要求 stderr 含这两个关键词）、退出码 2，且每个子命令 `-h` 免费获得（AC-LIST-9） |
| 持久化 | 标准库 `json` + 单个 JSON 文件 | PRD 6.2 明文规定；单文件可被 `cat` 读；无第三方运行时依赖（PRD 4.2-7、9 节） |
| 原子写 | `tempfile.mkstemp(dir=<目标目录>)` + `os.replace()` | PRD R-4；同目录保证 `os.replace` 是同一文件系统的原子重命名；`os.replace` 跨平台（Windows 亦可覆盖已存在文件） |
| 时间 | `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")` | PRD 5 节：UTC、ISO 8601、秒级、带 `Z`。直接格式化比 `isoformat()` 更可控（后者会输出 `+00:00` 与微秒） |
| 测试 | `pytest` + `tmp_path` + `monkeypatch.setenv` | PRD 11 节硬要求；另加 `subprocess` 端到端用例验证真实进程退出码 |
| 打包 | `pyproject.toml`（setuptools）+ `console_scripts: todo` | 满足 PRD 6.4；同时提供 `python -m todo_cli`（`__main__.py`）供未安装时使用 |
| 颜色输出 | v1 不做 | 属 PRD 4.3 P1，P0 一律纯文本，管道安全 |
| 文件锁 | v1 不做 | PRD R-9 与 12-12 明确列入已知限制，见第 6.5 节 |

不引入第三方运行时依赖（无 `click`/`typer`）：PRD 要求零依赖，且四个子命令的规模用 argparse 足够，减少供应链与安装成本。

## 3. 数据模型

### 3.1 文件结构（与 PRD 6.2 逐字一致）

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

写出参数：`json.dump(data, fh, ensure_ascii=False, indent=2)` + 结尾换行（用户可读、diff 友好）。读取时 UTF-8。

### 3.2 Python 内存表示

```python
Todo = dict            # {"id": int, "text": str, "done": bool, "created_at": str, ...未知字段保留}
Document = dict        # {"version": int, "next_id": int, "todos": list[Todo], ...未知字段保留}
```

### 3.3 校验规则（`load()` 内执行，任一不符 → `InvalidFileError`）

| 检查 | 不通过时的行为 |
| --- | --- |
| 顶层是 JSON 对象 | `invalid todo file`，退出码 1（AC-ERR-3 中 `{"todos":"oops"}` 命中的是 todos 类型检查） |
| `version` 存在且 `== 1` | 版本不支持 → `invalid todo file`，退出码 1，**原文件不写不删**（AC-ERR-2） |
| `next_id` 是 int 且 `>= 1` | 否则 `invalid todo file` |
| `todos` 是 list | 否则 `invalid todo file` |
| 每个 todo 是 dict，`id` 为正 int、`text` 为非空 str、`done` 为 bool、`created_at` 为 str | 否则 `invalid todo file` |
| todo 的 `id` 在文件内唯一 | 否则 `invalid todo file` |
| 不检查未知字段 | 读取时原样保留；写入时仍在（AC-ERR-6） |

### 3.4 文本清洗（`sanitize_text()`）

`" ".join(raw.split())`：等价于先 `strip()`、再把内部任意连续空白（含 `\n`、`\t`、全角空格以外的多空格）折叠为单个半角空格。满足 AC-ADD-3/4/5。清洗后为空字符串 → 用法错误。

## 4. 命令接口与函数签名

### 4.1 CLI 接口（对用户，= PRD 4.1）

```
todo [--file PATH] [--version] [-h] <command> [args]
  todo add TEXT [TEXT...]
  todo list [--all | -a]
  todo done ID
  todo delete ID
```

### 4.2 模块签名

`code/todo_cli/errors.py`

```python
class TodoError(Exception):                       # 基类，携带 exit_code
    exit_code: int = 1
class UsageError(TodoError):        exit_code = 2  # 用法错误：空文本、非法 ID
class NotFoundError(TodoError):     exit_code = 1  # ID 不存在
class ReadFileError(TodoError):     exit_code = 1  # 读失败 / 权限
class InvalidFileError(TodoError):  exit_code = 1  # JSON 非法 / 结构非法 / 版本不支持
class WriteFileError(TodoError):    exit_code = 1  # 写失败 / 目录不存在
```

`code/todo_cli/storage.py`

```python
def resolve_path(file_option: str | None) -> Path      # --file > TODO_FILE > ~/.todo.json，均 expanduser()
def load(path: Path) -> dict                            # R-1/R-2/R-3；文件不存在返回空文档，不建文件
def save(path: Path, document: dict) -> None            # R-4/R-5；原子写
def now_iso() -> str                                    # "2025-05-01T02:10:33Z"
def empty_document() -> dict                            # {"version": 1, "next_id": 1, "todos": []}
def sanitize_text(parts: list[str]) -> str              # 见 3.4
```

`code/todo_cli/models.py`

```python
class TodoStore:
    def __init__(self, document: dict) -> None
    @property
    def todos(self) -> list[dict]
    def find(self, todo_id: int) -> dict | None
    def add(self, text: str) -> dict          # 分配 new_id = next_id，next_id += 1（R-7）
    def mark_done(self, todo_id: int) -> tuple[dict, bool]   # (todo, changed)；changed=False 表示已完成的幂等分支
    def delete(self, todo_id: int) -> dict    # 抛出 NotFoundError
    def pending(self) -> list[dict]           # done == False，按 id 升序
    def sorted_all(self) -> list[dict]        # 按 id 升序（5 节排序规则）
    def to_document(self) -> dict             # 未知顶层字段保留
```

`code/todo_cli/cli.py`

```python
def build_parser() -> argparse.ArgumentParser
def main(argv: list[str] | None = None) -> int   # 返回退出码，不调用 sys.exit（PRD 5 节）
```

`code/todo_cli/__main__.py`：`raise SystemExit(main())`，使 `python -m todo_cli` 可用。
`code/todo_cli/__init__.py`：`__version__ = "0.1.0"`（AC-G-9 的 `--version` 一并实现，成本极低）。

### 4.3 每个命令的执行流程（伪代码级，非伪实现）

```
add:
  store = TodoStore(load(resolve_path(args.file)))     # R-1：不存在 = 空文档
  text  = sanitize_text(args.text_parts)               # 空 → raise UsageError
  todo  = store.add(text)
  save(path, store.to_document())                      # R-8：返回 0 前已落盘
  print(f"Added #{todo['id']}: {todo['text']}")
  return 0

list:
  store = TodoStore(load(path))                        # 只读，不 save（R-6）
  rows  = store.sorted_all() if args.all else store.pending()
  空且 --all      → print("No todos yet.")
  空且默认视图    → print("No pending todos.")
  否则           → print(f"{id}. [{'x' if done else ' '}] {text}") 逐行

done:
  todo, changed = store.mark_done(id)                  # 不存在 → NotFoundError
  if changed: save(...) ; print(f"Completed #{id}: {text}")
  else:       （不写盘）  ; print(f"Todo #{id} is already completed.")
  return 0

delete:
  todo = store.delete(id)                              # 不存在 → NotFoundError
  save(...)
  print(f"Deleted #{id}: {text}")
  return 0
```

`main()` 的外层结构：

```python
try:
    ... 分发 ...
except UsageError as exc:        # 退出码 2，stderr "Error: ..."
except TodoError as exc:         # 退出码 1，stderr "Error: ..."（消息内含 <abs_path>）
except BrokenPipeError:          # 管道提前关闭（如 `| head`）→ 静默退出码 0
```

`argparse` 的用法错误由 argparse 自己 `sys.exit(2)`：为满足"`main` 返回退出码"的可测试性，子解析器用 `exit_on_error=False` 不够（版本差异大），采用**捕获 `SystemExit` 并映射为返回码**的方式：`main` 内部包一层 `try/except SystemExit as exc: return exc.code`。这样 `cli.main([...])` 永不抛 SystemExit，测试直接拿到 2。注意 `-h/--help` 走同一条路，`exc.code == 0`（AC-G-7、AC-LIST-9 要求退出码 0 且内容在 stdout）。

## 5. JSON 读写、并发与损坏文件处理

| 场景 | 处理 |
| --- | --- |
| 文件不存在 | R-1：`load` 返回 `empty_document()`，**不创建文件**。只读命令（`list`）全程不 `save`，因此 `list` 不会创建文件、不改 mtime（AC-LIST-1/6）。首次写命令才落盘 |
| 读失败（权限/IO） | `ReadFileError`：`Error: cannot read todo file <abs_path>: <原因>`，退出码 1（R-2） |
| JSON 语法错误 | `InvalidFileError`：`Error: invalid todo file <abs_path>: <原因>`，退出码 1，原文件**逐字节不变**（AC-ERR-1） |
| 结构非法 / `version != 1` | 同上一行（AC-ERR-2/3）。**关键点**：校验在 `load()` 阶段完成，`save()` 根本不会被调用，因此不存在"读坏了再写回把用户数据清空"的路径 |
| 原子写 | `mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")` → 写入 + `flush` + `os.fsync` → `os.replace(tmp, path)`；任何异常都 `os.unlink(tmp)` 清理临时文件，原文件不动（R-4、AC-ERR-7） |
| 目标目录不存在 | `mkstemp` 抛 `FileNotFoundError` → 包装为 `WriteFileError`：`Error: cannot write todo file <abs_path>: 目录不存在`，退出码 1，**不自动建目录**（R-5、AC-ERR-5） |
| 只读文件 `chmod 444` | 顶层目录通常可写，但 `os.replace` 覆盖只读文件在 Linux 上仍可能成功——这取决于目录权限。为保证 AC-ERR-4 行为可预期，`save()` 在写临时文件前先做一次 `os.access(path, os.W_OK)` 检查，不可写即报 `cannot write todo file`，退出码 1。**不修改原文件权限** |
| 写失败（磁盘满等） | `WriteFileError`，退出码 1，临时文件被清理，原文件保持旧内容 |
| 并发 | **v1 不做文件锁**（R-9、12-12）：两个进程同时写会"后写覆盖先写"。已在 README 与本文件登记为已知限制。缓解措施（未来 v1.1）：`fcntl.flock` / `msvcrt.locking` 包一层独占锁；不在 v1 做，因为会引入平台分支与死锁风险，与"简单"冲突 |
| 部分写入/进程被杀 | 原子写保证文件内容是"旧完整内容"或"新完整内容"，不会出现半截 JSON（AC-ERR-7） |

ID 与排序：`new_id = next_id; next_id += 1`，与 `todos` 的变更在同一次 `save` 中落盘（R-7）。删除只从 `todos` 移除，**不改 `next_id`** → ID 不复用（AC-DEL-3）。所有输出按 `id` 升序。

## 6. 错误处理与退出码

### 6.1 退出码契约（PRD 5 节）

| 码 | 含义 | 触发点 |
| --- | --- | --- |
| 0 | 成功 | 正常完成；`--help`/`-h`；`done` 命中"已完成"幂等分支 |
| 1 | 运行时/业务错误 | ID 不存在；读/写文件失败；文件格式或版本非法 |
| 2 | 用法错误 | 未知子命令；缺必需参数；文本清洗后为空；ID 不是正整数；无子命令调用 |

### 6.2 输出流分工

- **stdout**：只放正常业务输出（`Added #...`、列表行、`No pending todos.`、帮助）。
- **stderr**：只放错误，统一前缀 `Error: `（argparse 自带的 `usage:` 行除外，PRD 7.2 已豁免，只要求退出码 2 + 关键词）。
- 错误路径下 stdout 必须为空（AC-DONE-3 显式断言）。

### 6.3 固定文案（PRD 7.2 映射，实现在 `cli.py` 常量区）

| 常量 | 文案 |
| --- | --- |
| `MSG_ADDED` | `Added #{id}: {text}` |
| `MSG_COMPLETED` | `Completed #{id}: {text}` |
| `MSG_ALREADY_DONE` | `Todo #{id} is already completed.` |
| `MSG_DELETED` | `Deleted #{id}: {text}` |
| `MSG_NO_PENDING` | `No pending todos.` |
| `MSG_NO_TODOS` | `No todos yet.` |
| `MSG_NOT_FOUND` | `Error: todo #{id} not found.` |
| `MSG_EMPTY_TEXT` | `Error: todo text cannot be empty.` |
| `MSG_BAD_ID` | `Error: '{raw}' is not a valid todo id.` |
| `MSG_READ_FAIL` | `Error: cannot read todo file {path}: {reason}` |
| `MSG_INVALID_FILE` | `Error: invalid todo file {path}: {reason}` |
| `MSG_WRITE_FAIL` | `Error: cannot write todo file {path}: {reason}` |

### 6.4 非法 ID 的处理细节

`done`/`delete` 的 ID 用**自定义 `type=parse_id`**，抛 `argparse.ArgumentTypeError` 会被 argparse 转成用法错误（退出码 2）——但文案需为 `Error: '<输入值>' is not a valid todo id.`，因此改为：

1. 位置参数按 `str` 接收，在 handler 内手工校验；
2. 校验失败 → 抛 `UsageError(MSG_BAD_ID.format(raw=raw))` → `main` 捕获后写 stderr 并返回 2。

这样 `todo done abc`（AC-DONE-4）、`todo done 0`（AC-DONE-5）、`todo delete -1` 三类都给出**同一条**文案与退出码 2。注意 `-1` 会被 argparse 误判为选项，需要 `--` 分隔或接受 argparse 的用法错误（退出码 2 依然满足 AC）。实现上使用 `parse_known_args` 之外的手段：把该位置参数名声明为 `id_raw`，并在 `main` 中把 argparse 的 `SystemExit(2)` 也算作满足 AC 的路径——两种入口都返回 2，AC 均通过。

### 6.5 已知限制（明确不承诺）

1. 并发写不互斥，可能互相覆盖（PRD R-9 / 12-12）；
2. 不做配置文件、别名、默认值持久化（PRD 12-5）；
3. 颜色、`list --done`、`0600` 权限、文本截断、`completed_at` 为 P1，未实现（`--version` 除外，已顺带实现）。

## 7. 测试策略

| 层面 | 做法 |
| --- | --- |
| 隔离 | 每个用例用 `tmp_path` 生成独立 JSON 路径；优先 `cli.main(["--file", str(p), ...])`，另一部分用 `monkeypatch.setenv("TODO_FILE", str(p))` 覆盖环境变量路径 |
| 真实数据保护 | 会话级 `autouse` fixture 断言 `TODO_FILE` 未指向 `Path.home()/".todo.json"`；另有一条测试断言运行测试后 `~/.todo.json` 不存在（若原本不存在）。`HOME` 用 `monkeypatch.setenv` 指向 `tmp_path` 以验证默认路径（AC-G-4）与 `~` 展开（AC-G-8） |
| 断言方式 | 显式断言 `rc` + `capsys.readouterr()` 的 stdout/stderr；成功文案逐字断言；argparse 相关只断言关键词（`usage`、`invalid choice`） |
| 只读性 | AC-LIST-6 / AC-ERR-1/2/3 断言文件 bytes 与 `st_mtime_ns` 不变 |
| 端到端 | ≥1 条用 `subprocess.run([sys.executable, "-m", "todo_cli", ...], cwd=code/)` 验证真实进程退出码与输出 |
| 覆盖清单 | PRD 11 节列出的 AC-ADD-1..9、AC-LIST-1..9、AC-DONE-1..8、AC-DEL-1..7、AC-G-1..8、AC-ERR-1..6，加 1 条数据完整性用例（连续操作后校验 `version`/`next_id`/`created_at` 正则/ID 单调性） |
| 完成定义 | `pytest -q` 全绿、无 skipped、不产生 `~/.todo.json` |

AC-ERR-4（只读文件）与 AC-ERR-7（kill -9）说明：AC-ERR-4 用 `os.chmod(path, 0o444)` 测试；若以 root 运行（CI 容器常见）`os.access` 仍报可写，实现侧的 `os.access` 前置检查在 root 下会失效，测试需 `pytest.mark.skipif(os.geteuid() == 0)` 并注明原因（PRD 允许"需特权条件的用例"带注释跳过）。AC-ERR-7 属手工验证项，测试以"临时文件被清理、目标文件始终是完整 JSON"的等价断言替代。

## 8. 与 PRD 的差异（需 CEO 知悉）

| # | PRD | 本方案 | 理由 |
| --- | --- | --- | --- |
| D-1 | 6.4 建议 `tests/` 放在仓库根 | 放在 `code/tests/` | CEO 指令要求代码统一交付在 `code/`；PRD 6.4 自述"非强制"。`pyproject.toml` 已配置 `testpaths = ["tests"]`，在 `code/` 下直接 `pytest` 即可 |
| D-2 | AC-G-9 `--version` 定为 P1 | v1 一并实现 | 成本 3 行，且便于 CEO 验收安装是否成功 |
| D-3 | PRD 7.2 未定义"无子命令"文案 | 复用 argparse 的用法错误（stderr + 退出码 2） | PRD 5 节只要求"打印简短用法到 stderr，退出码 2"，未规定逐字文案 |
| D-4 | AC-ERR-4 期望 `chmod 444` 时写失败 | root 用户下 `os.access` 与 `os.replace` 都可能成功 | 属平台权限语义差异，测试按条件跳过并注释；非 root 环境完全满足 AC |

## 9. 开发里程碑

| 阶段 | 产出 | 验证方式 |
| --- | --- | --- |
| T1 | `storage.py` + `errors.py`（路径解析、load、原子 save、校验） | 单元测试：不存在文件返回空文档；损坏文件抛 `InvalidFileError` 且文件不变；临时文件被清理 |
| T2 | `models.py`（TodoStore：add/done/delete/排序/ID 不复用/未知字段保留） | 单元测试：next_id 递增、删除后不复用、幂等 done 返回 changed=False |
| T3 | `cli.py` + `__main__.py` + `pyproject.toml`（四个子命令、文案、退出码、help） | 逐条对照 PRD 8 节的 AC 编写 pytest |
| T4 | `code/tests/test_cli.py` + `code/tests/test_storage.py`（含 subprocess 端到端） | `cd code && python -m pytest -q` 全绿 |
| T5 | `code/README.md`（安装、用法、测试结果、已知限制） | CEO 按 PRD 第 14 节手工脚本验收 |

## 10. 假设

| # | 假设 |
| --- | --- |
| A-1 | 沿用 PRD A-1：默认数据文件 `~/.todo.json`，`--file` > `TODO_FILE` > 默认 |
| A-2 | 输出文案英文、待办文本 UTF-8 原样（PRD 5 节），不做本地化 |
| A-3 | 环境提供 Python 3.9+；pytest 作为开发依赖需单独安装（`pip install pytest`），不进 `install_requires` |
| A-4 | 未做文件锁，并发写覆盖为已知限制，README 与 v1 文案均不承诺并发安全 |


## 11. 本次验收环境与残余风险

| 项 | 记录 |
| --- | --- |
| 验收环境 | Windows 10 + Python 3.10.6 + pytest 9.1.1（本机唯一解释器） |
| 测试结果 | `cd code && python -m pytest -q` → 98 passed，无 skip / warning / xfail |
| 手工复核 | 用临时文件逐条跑 add/list/done/delete，退出码 0/1/2 与 PRD 7.2 文案逐字一致；`list` 不创建文件；ID 不复用（删 #2 后新增为 #3） |
| 数据安全 | 测试期间真实 `~/.todo.json` 未被创建（测试用 `tmp_path` + autouse 守卫 fixture） |
| 残余风险 1 | Linux / macOS 未实测（PRD 9 节要求必须通过）。实现只用跨平台 API，未用 Unix-only 接口，风险低；建议 CI 补一条 Linux/macOS 的 pytest |
| 残余风险 2 | Python 3.9 未实测（本机无 3.9）。已用 `ast.parse(feature_version=(3, 9))` 静态核查全部源文件通过，注解用 `# type:` 注释式；建议在 3.9 环境补跑一次 |
| 残余风险 3 | AC-ERR-4（只读文件）依赖 `os.access(os.W_OK)` 前置检查。以 root 运行时该检查会失效（root 可写任何文件），测试已按 `os.geteuid() == 0` 条件 skip；非 root 环境完全满足 AC |
| 残余风险 4 | 未做文件锁，并发写可能互相覆盖（PRD R-9 / 12-12 已列为已知限制，v1 不承诺并发安全） |
