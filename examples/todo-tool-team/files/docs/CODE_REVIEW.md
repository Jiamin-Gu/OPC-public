# 代码审查报告：`code/todo.py`（todo CLI 第一版）

- 审查人：🧪 测试
- 审查对象：`code/todo.py`（工程师当前版本）、`code/README.md`
- 对照依据：`docs/PRD.md`（v1.0，§5/§6/§8/§9）、`docs/TECH_DESIGN.md`（v1.0，§3/§4/§5/§6）
- 关联产出：`docs/TEST_PLAN.md`（用例 TC-x 与本文 DEF-x 一一对应）
- 审查方式：**静态阅读源码 + 逐条比对文档契约**（我这一轮没有执行代码的环境，凡结论标注「需实跑确认」的，请按 TEST_PLAN §7 的命令验证后回填）

---

## 1. 审查结论

**总体质量：中上。** 架构分层、原子写、退出码设计都对得起「第一版可用」，代码整洁、注释与文档意识强，命令层与 IO 解耦得很干净（这点直接让测试成本大幅下降）。

**发布建议：先修 1 个 P0 再验收，不建议当前版本直接进验收。**

| 类别 | 数量 | 说明 |
| --- | --- | --- |
| P0 阻断 | **1** | DEF-01：无子命令时 `TodoError` 逃逸 → 打印 traceback、退出码 1（应为 2）。TC-E01 会失败 |
| P1 严重 | 2 | DEF-02 读宽容放行空/超长文本（写回文件不满足自己声明的 schema）；DEF-03 深嵌套 JSON 触发 `RecursionError` 未捕获 |
| P2 一般 | 3 | DEF-04 符号链接目标被替换；DEF-05 重复 `done` 也写盘；DEF-06 `main()` 参数错误时抛 `SystemExit` 而非返回退出码 |
| P3 轻微 | 3 | DEF-07 BrokenPipe 收尾 flush 噪音；DEF-08 崩溃残留临时文件；DEF-09 错误文案丢失选项名 |
| 待实测确认 | 1 | DEF-10 `add -x`（`-` 开头内容）的 argparse 行为未文档化 |
| 文档一致性 | 5 | C-1~C-5，见第 3 节（含 1 个必须 CEO 决策项） |

**通过项（正面确认，逐条核对过）**：

- ✅ PRD §5/§6 定义的**全部核心输出文案逐字一致**（`已添加 #id: …`、`<id>. [ ] …`、`<id>. [x] …`、`暂无待办事项`、`已完成 #id: …`、`#id 已是完成状态`、`已删除 #id: …`、`错误：找不到 ID 为 id 的待办`、`错误：待办内容不能为空`、`错误：数据文件已损坏，路径：…`）。
- ✅ 退出码语义与 PRD §5.6 / 技术方案 §4.4 一致（0 成功、1 找不到 ID、2 用法错误、3 数据层错误），且常量集中定义、无散落魔数。
- ✅ 成功信息走 stdout、错误信息走 stderr（脚本可 `2>/dev/null`），`--help` 走 stdout 且退出 0。
- ✅ **原子写**：同目录 `mkstemp` → `json.dump` → `flush` → `os.fsync` → `os.replace`，`finally` 清理临时文件；任何写入异常都不破坏原文件。
- ✅ **损坏文件绝不被覆盖**：`load_state` 抛错即终止，永远走不到 `save_state`（AC-6 的核心诉求成立）。
- ✅ 路径**运行时解析**（不 import 时固化），因此 `--file` / `TODO_FILE` 可注入，测试隔离可行（技术方案 §7.5 的关键设计，落地正确）。
- ✅ 严格分层：命令层 `cmd_*` 只吃 `state` 字典、只返回文案，不碰文件、不打印，可脱离文件系统单测。
- ✅ 零第三方依赖；语法/API 均在 Python 3.8 可用范围（无 walrus、无 `match`、`typing` 用 `Dict/List`），符合 PRD §9。
- ✅ P0/P1 功能齐备：四条命令、`list --all`、空清单提示、参数校验、帮助；P2（clear/edit/priority/搜索）**未实现**，与 PRD §10 的 out-of-scope 一致，无范围蔓延。

---

## 2. 需求一致性审查

### 2.1 文案与退出码逐条对照（全部通过）

| 场景 | PRD 要求 | 代码实现 | 结论 |
| --- | --- | --- | --- |
| 添加成功 | `已添加 #<id>: <内容>` | `f"已添加 #{todo_id}: {text}"` | ✅ 逐字一致 |
| 列出未完成 | `<id>. [ ] <内容>` | `f"{id}. [{mark}] {text}"`，`mark=" "` | ✅ |
| 列出已完成 | `<id>. [x] <内容>` | `mark="x"` | ✅ |
| 空清单 | `暂无待办事项` | 同 | ✅ |
| 完成成功 | `已完成 #<id>: <内容>` | 同 | ✅ |
| 重复完成 | `#<id> 已是完成状态`（退出 0，非错误） | 同，退出 0 | ✅ |
| 删除成功 | `已删除 #<id>: <内容>` | 同 | ✅ |
| ID 不存在 | `错误：找不到 ID 为 <id> 的待办`，退出 1 | 同 | ✅ |
| 内容为空 | `错误：待办内容不能为空`，退出 2 | 同 | ✅ |
| 数据损坏 | `错误：数据文件已损坏，路径：<path>`，退出 3 | 同 | ✅ |
| 缺参数 | 帮助 + `错误：<原因>`，退出 2 | `metavar="<id>"` + `error()` 翻译表 → `错误：缺少参数 <id>` | ✅ 逐字一致 |

### 2.2 数据文件路径：三方冲突（**必须 CEO 决策**）

| 来源 | 默认路径 | 指定方式 | `--file` 是否支持 |
| --- | --- | --- | --- |
| `docs/PRD.md` §5.5 / AC-1 / AC-5 | `~/.todo.json` | 环境变量 `TODO_FILE` | ❌ 未定义 |
| `docs/TECH_DESIGN.md` §4.1 / §6.1 | `~/.todo.json` | 环境变量 `TODO_FILE` | ❌ **技术方案完全没写 `--file`** |
| `docs/TECH_DESIGN.md` §3.2 | `DEFAULT_FILENAME = ".todo.json"`（与自己的 §6.1 不符） | — | — |
| **代码 / README** | **当前目录 `./todos.json`** | `--file/-f > TODO_FILE > ./todos.json` | ✅ |

- **影响**：按 PRD AC-1「断言 `~/.todo.json` 的 `todos` 长度增加」写的用例，在当前代码上必然失败；按代码写的用例在 PRD 面前又是「未定义接口」。这是本批交付里**唯一会让验收结论产生歧义**的问题。
- **处置建议（不改代码也能先测）**：CEO 拍板后，同步修改 `PRD §5.5 / AC-1 / AC-5`、`TECH_DESIGN §3.2 / §4.1 / §6.1`、`README §4.1` 四处。测试侧我已把主用例全部改为「显式 `--file` 指向临时文件」，因此**不阻塞**测试执行（见 TEST_PLAN §7.5 的 TC-P 组）。
- **我的倾向性建议**（供 CEO 参考）：若定位是「终端开发者的个人待办」，家目录更稳（不随 cwd 漂移、不会误提交进仓库）；`./todos.json` 更适合「项目级待办」。二选一都可以，但**必须只留一个默认值**，否则用户会以为数据丢了。

### 2.3 错误码覆盖面超出 PRD（需 PRD 补充，非代码缺陷）

PRD §5.6 只定义了 5 类错误，实现额外定义了以下错误，均**合理**但 PRD 未记载，建议产品补一节「错误码扩展」表：

| 实现新增错误 | 退出码 | 评价 |
| --- | --- | --- |
| 内容超长（>500） | 2 | 合理；PRD 原文是「建议长度上限」，实现成了硬校验（技术方案 D-1，待确认） |
| 重复 ID / 版本不支持 / 非 UTF-8 / 路径是目录 / 无权限 / 写失败 | 3 | 合理，且文案都带路径，可定位 |
| Ctrl+C → `错误：操作已取消` | 130 | 合理（POSIX 惯例） |
| 管道关闭 → 静默退出 0 | 0 | 合理 |
| 未知子命令 / 未知参数 / 缺值 | 2 | 合理 |

### 2.4 技术方案与代码不一致（文档需回改）

| 编号 | 位置 | 技术方案写的 | 代码实际 |
| --- | --- | --- | --- |
| C-4 | §3.2 | `DEFAULT_FILENAME = ".todo.json"` | `DEFAULT_FILENAME = "todos.json"` |
| C-5 | §3.2 | `resolve_data_path(environ)` | `resolve_data_path(cli_file=None, environ=None)`（多了 `cli_file`，且优先于环境变量） |
| C-5 | §4.1 | 「不实现 `--version`、短选项别名（除 `-a`）」 | 实际还有 `-f` 短选项 |
| C-1~C-3 | §4.1 / §6.1 | 只有 `TODO_FILE`，默认 `~/.todo.json` | `--file > TODO_FILE > ./todos.json` |

> README §6 已经诚实地把差异列成表（C-1~C-3）并指明「需 CEO 拍板后由产品/工程同步文档」——这一点做得很好，请保持。

### 2.5 范围与假设一致性

- PRD 假设 A3（`list` 默认隐藏已完成）、A4（删除无二次确认、ID 不复用）、A5（重复完成不报错）→ 实现全部一致 ✅。
- PRD §10 的 10 项 out-of-scope → 均未实现 ✅。

---

## 3. 缺陷清单

### DEF-01 [P0 / 必修] 无子命令时异常逃逸：打印 traceback，退出码 1（应 2）

- **位置**：`main()`。`if args.command is None: usage_error(...)` 写在 `try:` **之前**。
- **根因**：`usage_error()` 打印帮助后 `raise TodoError(message, 2)`，但这个调用点不在 `try/except TodoError` 覆盖范围内；`run()` 只捕获 `KeyboardInterrupt` 与 `BrokenPipeError`，于是 `TodoError` 直接冒到解释器 → 打印 Python traceback、退出码 1。
- **复现**（1 条命令即可确认）：
  ```bash
  python code/todo.py; echo "exit=$?"
  # 期望：stderr = 帮助 + "错误：缺少子命令（可用：add、list、done、delete）"，exit=2
  # 实际（静态判定）：stderr = 帮助 + Traceback ... todo.TodoError，exit=1
  ```
- **影响**：违反 PRD §5.6 / 技术方案 §4.2、§4.3（缺子命令必须退出 2 且输出中文提示）；用户看到一个「程序崩了」的假象；TEST_PLAN TC-E01 直接失败。这是**脚本可判定**的行为差异，不是风格问题。
- **修法（推荐 a）**：
  a. 把 `if args.command is None:` 判断移进 `try` 块内（最小改动，1 行位置调整）；
  b. 或在 `run()` 内加一层 `except TodoError as exc: print(exc.message, file=sys.stderr); code = exc.exit_code`（兜底，顺带把「所有 TodoError 都出口统一」变成事实）；
  c. 不建议在 `usage_error()` 里直接 `sys.exit(2)`（会让 `main()` 失去「返回 int」的可测性，见 DEF-06）。
- **顺带**：修复后请在 `main()` 加一条针对「无子命令」的回归断言。

### DEF-02 [P1] 读宽容放行空文本/超长文本，写回的文件不满足自己声明的 Schema

- **位置**：`normalize_state()` 只校验 `text` 是 `str`，未校验非空与长度上限。
- **现象**：手工编辑文件为 `"text": ""`（或 800 字），命令正常运行；之后任何写命令（`add/done/delete`）都会把这份**不合法数据**原样写回。而技术方案 §5.2 的 JSON Schema 明确要求 `"minLength": 1, "maxLength": 500`，并声明「write 出来的文件一定满足该 schema」。
- **影响**：用 Schema 校验做测试的用例会出现「假绿」（读得进来却写得出不合规数据）；若后续版本按 Schema 严格解析，用户的历史文件会突然报错。
- **建议**：`text == ""` 或 `len(text) > 500` 时按「数据文件已损坏」（退出 3，文案带路径）处理，或显式截断并打印警告。**并同步技术方案 §5.4 的容错表**（该表目前没写 `text` 为空这一行）。

### DEF-03 [P1] 深层嵌套 JSON → `RecursionError` 未捕获

- **位置**：`load_state()` 只捕获 `json.JSONDecodeError`。
- **现象**：`json.loads` 对极深嵌套（如数万层 `[[[[…]]]]`）抛 `RecursionError`（`ValueError`/`RuntimeError` 家族，**不是** `JSONDecodeError`）→ 逃逸 → traceback + 退出 1，与「数据层错误统一退出 3」的策略不符（文件不会被覆盖，数据是安全的，这点没问题）。
- **建议**：`load_state` 里补 `except RecursionError:` → `corrupt_error(path)`。成本 2 行。

### DEF-04 [P2] 数据文件是符号链接时，链接会被替换成普通文件

- **位置**：`save_state()` 用 `os.replace(tmp, path)`。
- **现象**：若 `~/.todo.json` 是指向 `~/Dropbox/todo.json` 的软链（很常见的同步/备份玩法），`add` 之后目标路径变成一个**独立的普通文件**，Dropbox 里的原文件不再更新，用户会以为同步坏了。
- **建议（择一）**：① 写入前 `path = path.resolve()`（保持软链语义，需明确这是预期）；② 检测到 `path.is_symlink()` 直接报错提示用户；③ 至少在 README「已知限制」写清楚。**最低要求：写进已知限制。**

### DEF-05 [P2] 重复 `done` 也写盘，把「无变化」变成可能失败的操作

- **位置**：`main()` 中 `done` 分支无条件 `save_state()`；`cmd_done()` 对已完成项提前返回但没告知调用方「无变化」。
- **现象**：重复 `done` 语义不变（AC-3 要求「数据不变」按解析后 JSON 成立 ✅），但：① 文件 mtime 被刷新；② 用户手工排版的文件被整体重写、格式被规范化；③ 若数据文件在只读目录，一个「本来就已完成」的幂等操作会报退出 3（本可成功）。
- **建议**：让命令层返回 `(message, dirty)` 或在 `main` 里比较写前后是否需要落盘；技术方案 D-6 已把它列为待决策项，建议**改为「无变化不写盘」**。

### DEF-06 [P2] `main()` 在参数错误时抛 `SystemExit` 而非返回退出码

- **位置**：`TodoArgumentParser.error()` 抛 `SystemExit(2)`；`main()` 的返回类型标注是 `int`。技术方案 §3.2 也把 `main` 描述为「返回进程退出码」。
- **影响**：黑盒测试无影响；但白盒单测 `from todo import main; main([])` 会抛异常而不是返回 2，需要 `pytest.raises(SystemExit)` 特判，也让「统一错误出口」的说法打折扣。
- **建议**：在 `main()` 里 `try: args = parser.parse_args(argv) except SystemExit as exc: return int(exc.code or 0)`；或在文档中明确「参数解析错误是 main 的例外路径」。

### DEF-07 [P3] 管道提前关闭时收尾 flush 会打印 `Exception ignored … BrokenPipeError`

- **位置**：`run()` 捕获了 `BrokenPipeError` 并 `flush` 后 `sys.exit(0)`，但解释器退出时仍会再 flush 一次已损坏的 stdout。
- **现象**：`list | head -1` 场景下，除了正常输出，stderr 可能多一行 `Exception ignored … BrokenPipeError`（不影响退出码）。
- **建议**：标准做法是在捕获后把 stdout 句柄重定向到 `os.devnull`（`os.dup2`）再退出；或在文档中把它列为已知噪音。属体面问题，不阻塞验收。

### DEF-08 [P3] 进程被强杀时残留 `.todo-*.tmp`

- **位置**：`save_state()` 的 `finally` 只能覆盖 Python 异常，覆盖不了 SIGKILL/断电。
- **现象**：数据目录里可能积累随机名的 `.todo-xxxx.tmp`（写入不损坏数据，只是卫生问题）。
- **建议**：写盘前顺手清理同目录下「同前缀且超过 1 小时」的临时文件；或改用固定名 `todos.json.tmp`（⚠ 但固定名在并发下会互相踩，v1 无锁，如改需保留随机后缀）。

### DEF-09 [P3] 错误文案丢失选项名

- **现象**：`todo --file`（缺值）翻译后只输出 `错误：参数值缺失`，没说是哪个选项（argparse 原文 `argument -f/--file: expected one argument` 里的选项名被翻译表切掉了）。
- **建议**：翻译时保留前缀，例如 `错误：参数 --file/-f 缺少值`。可读性优化，不影响验收。

### DEF-10 [待实跑确认] 以 `-` 开头的内容无法直接作为 `add` 的值

- **现象**：`todo add -x` 会被 argparse 当作选项处理（报「未知参数」或「缺少参数」），必须写成 `todo add -- -x`。这是各版本 argparse 的通用行为，但 README 未说明。
- **建议**：README「命令一览」补一句「内容以 `-` 开头时用 `--` 分隔」，并在 TEST_PLAN TC-B13 实测后回填。（此条我未实跑，标注为待确认。）

---

## 4. 数据持久化专项审查

| 关注点 | 现状 | 评价 | 建议 |
| --- | --- | --- | --- |
| 原子性 | 同目录 `mkstemp` → `fsync` → `os.replace` | ✅ 最佳实践，同文件系统重命名保证不半写 | 保持 |
| 目录不存在 | `mkdir(parents=True, exist_ok=True)` | ✅ 顺手，失败映射为退出 3 | 保持 |
| 数据安全优先级 | 读取失败即终止，绝不写回 | ✅ AC-6 成立，这是本实现最值得肯定的点 | 保持，并在测试中做**字节级不变**断言 |
| 编码 | UTF-8 + `ensure_ascii=False` + 末尾单换行 + `indent=2` | ✅ 中文原样、可读、利于 diff | 保持 |
| 文件权限 | `mkstemp` 落地 → 0600 | ⚠ 与常见 0644 不同；会让「原本 0644 的文件」在写入后变成 0600 | 待 CEO 决策 D-4；建议保留 0600（个人数据更安全）并在 README 明说 |
| 符号链接 | `os.replace` 替换链接本身 | ❌ 见 DEF-04 | 至少文档化 |
| 并发写 | 无锁 | ⚠ PRD 已声明不做；原子替换保证「不损坏、不半写」，最坏丢一次更新 | 保持 v1 现状，但把「多终端同时用可能丢更新」写进 README 已知限制（README §7 已写 ✅） |
| 性能 | 每次命令全量读 + 全量写 | ✅ 千条量级没问题（PRD 目标） | 万条以上再考虑；可在 README 注明规模假设 |
| 临时文件生命周期 | 异常路径清理；SIGKILL 不清理 | ⚠ 见 DEF-08 | 加一次惰性清理 |
| 只读文件 / 只读目录 | 只读文件可被替换（目录可写）；只读目录退出 3 | ✅ 行为合理且已文档化 | 测试用例 TC-E18/E19 需在 root 下跳过 |

---

## 5. 异常处理专项审查

| 场景 | 是否被捕获 | 退出码 | 评价 |
| --- | --- | --- | --- |
| 文件不存在 / 空文件 | ✅ | 0（视为空清单） | 合理（技术方案 D-3） |
| `IsADirectoryError` | ✅ | 3 | 文案带路径，好 |
| `PermissionError`（读） | ✅ | 3 | 好 |
| `UnicodeDecodeError` | ✅ | 3 | 好（注意它是 `ValueError` 子类，代码放在了 `except OSError` 之前，顺序正确） |
| 其它 `OSError`（读/写） | ✅ | 3 | 好 |
| `JSONDecodeError` / 顶层非对象 / `todos` 非数组 / `id`、`text`、`done` 类型错误 / 重复 ID / `version` 不支持 | ✅ | 3 | 覆盖面完整 |
| `next_id` 缺失或不一致 | ✅ 归一化为 `max(id)+1` | 0 | 好（兼容手工编辑） |
| `KeyboardInterrupt` | ✅ | 130 | 好 |
| `BrokenPipeError` | ✅（有收尾噪音） | 0 | 见 DEF-07 |
| `RecursionError`（深嵌套 JSON） | ❌ **未捕获** | 1（traceback） | 见 DEF-03 |
| 未知子命令 / 缺参数 / 非法 id / 内容为空/超长 / `--file` 为空 | ✅ | 2 | 好（且帮助同时打 stderr） |
| **无子命令** | ❌ **捕获点缺失** | **1（traceback）** | **见 DEF-01（必修）** |
| `MemoryError` / 其它未预期异常 | ❌ 不捕获（有意为之，技术方案 §6.7） | 1 + traceback | 可接受（便于定位缺陷），但 DEF-01 属于「可预期」情形，不适用此豁免 |

**结论**：异常处理框架设计是好的（单一 `TodoError` + 集中在 `main` 出口），**唯一的破口就在 DEF-01**：恰好有一个 `usage_error()` 调用点被放在了 `try` 之外。这是典型的「一处位置写错，覆盖全盘策略」的缺陷，修复成本极低。

---

## 6. 测试友好性评估（写给工程师）

**做得好的地方（请保持）**：

1. 路径运行时解析 + `--file`/`TODO_FILE` 双通道 → 测试可以完全隔离，不碰真实家目录。
2. 命令层纯函数（只吃 `state`、只回文案）→ 边界值可以走白盒快测。
3. `normalize_state()` 可单独调用 → 畸形数据可以不走文件系统直接测。
4. 帮助/错误分流到 stdout/stderr，且退出码是常量 → 断言稳定。

**建议增强（低成本、收益高）**：

| 建议 | 收益 | 优先级 |
| --- | --- | --- |
| 让 `now_string()` 可注入（如模块级变量或 `main` 传入 clock） | 可以精确断言 `created_at`/`done_at`，现在只能做正则断言 | 中 |
| `main()` 不通过 `SystemExit` 退出（DEF-06） | 白盒单测能直接拿到退出码 | 中 |
| 提供一个只读的「打印当前数据文件路径」开关（如 `--print-file`） | 脚本/测试可直接定位数据文件，避免靠猜默认路径 | 低（PRD 外，可不做） |
| README §8 里 `import todo` 的示例补一句 `sys.path` 处理 | 现在从仓库根直接 `import todo` 会失败（模块在 `code/` 下） | 低 |

---

## 7. 风险登记（代码外）

| ID | 风险 | 建议责任方 |
| --- | --- | --- |
| R-1 | 默认路径三方冲突未同步 → 后续所有文档/用例按不同基准写 | CEO 决策 → PM 改 PRD → 工程师改 TECH_DESIGN/README |
| R-2 | 默认 `./todos.json` 可能被 `git add` 提交，泄漏个人待办；`code/` 目录下还有可能出现测试残留文件 | 工程侧加 `.gitignore`（`todos.json`、`.todo-*.tmp`） |
| R-3 | 仓库的 `code/todo.py` 与共享文件区同名文件并存（工程师提到曾有「参考实现」），存在版本混淆 | 工程师确认哪份是唯一真源 |
| R-4 | 「全部已完成」与「清单为空」文案相同（`暂无待办事项`），用户可能误以为数据丢失 | PM 评估 v1.1 是否区分提示 |
| R-5 | 500 字硬上限、空文件容忍、0600 权限、重复 done 写盘 4 项决策未定 → 与 PRD 存在文字偏差 | CEO 批量确认（TEST_PLAN §10 已列选项） |

---

## 8. 改进建议优先级（可直接排期）

| 优先级 | 建议 | 对应缺陷 | 预估成本 |
| --- | --- | --- | --- |
| P0 必做 | 把「无子命令」判断移入 `try`（或 `run()` 兜底 `TodoError`） | DEF-01 | 1 行 |
| P0 必做 | 补一条针对无子命令的回归断言 | DEF-01 | 1 个用例 |
| P1 建议做 | `normalize_state` 校验 `text` 非空与长度；同步技术方案 §5.4 | DEF-02 | 3–5 行 |
| P1 建议做 | `load_state` 捕获 `RecursionError` → 退出 3 | DEF-03 | 2 行 |
| P2 排期做 | 符号链接行为：resolve 或显式报错，或写入已知限制 | DEF-04 | 1–3 行 |
| P2 排期做 | 重复 `done` 不写盘（命令层返回 dirty 标记） | DEF-05 | 5–10 行 |
| P2 排期做 | `main()` 返回退出码而非抛 `SystemExit` | DEF-06 | 3 行 |
| P3 可选 | BrokenPipe 静音、临时文件惰性清理、错误文案带选项名、README 补 `--` 用法 | DEF-07~10 | 各 1–5 行 |
| — 文档 | 按第 2.2/2.4/2.3 节同步 PRD 与 TECH_DESIGN（含 `--file`、默认路径、扩展错误码表） | C-1~C-5 | 文档改动，无代码风险 |

---

## 9. 审查局限（如实声明）

1. 本次为**静态审查**，我没有运行 `code/todo.py`。DEF-01、DEF-03 的根因由代码路径静态判定（`usage_error` 调用点在 `try` 之外；`RecursionError` 不在捕获列表），置信度高；DEF-10 属「依 Python 版本的 argparse 行为」，需实跑确认。
2. 未做性能实测、未做 Windows 实测、未做并发实测（对应 TEST_PLAN 的 TC-M02/M03/M04）。
3. `docs/TECH_DESIGN.md` 提到的「参考实现」与当前 `code/todo.py` 是否为同一份、是否有覆盖，我无法核实（共享文件区只有一个 `code/todo.py`），已列为风险 R-3。
4. 请按 `docs/TEST_PLAN.md` §13 的命令做一轮实跑，将结果回填本文档，并把「静态判定」更新为「已实测确认」——我会以此作为后续回归的基线。
