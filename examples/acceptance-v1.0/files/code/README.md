# 轻记 CLI 记账工具（code/ 使用说明）

一个只在本机运行、只支持人民币的记账工具：记一笔（`add`）、按月汇总（`summary`）、按分类统计（`stats`）。数据存在本地 JSON 文件里，不做账号、不做联网、不装第三方包。

本期唯一实现依据：`docs/PRD.md`（v1.1）与 `docs/技术方案.md`（v1.2）。两者与本文冲突时，以文档为准。

版本引用说明（返工修正）：仓库里 `docs/技术方案.md` 的**最新版本就是 v1.2**（首行「版本：v1.2（2026-10-23）」），没有更晚的文稿；本文此前误写为 v1.3，已改回 v1.2，全文引用的版本号都能在 `docs/` 下找到对应文稿。**唯一的例外**是「`--date` 传空串/纯空白按非法日期报错」这一条：技术方案 4.3 只写了 `--date`「否｜本机今天（本地时区）｜真实存在的日期，必须补零」，**没有**显式空值这一行，该行为来自本轮测试审查意见与本实现的显式校验（属实现侧补充口径，已实测并记入 `code/verify.log` 4-E/4-F），已列入本文「待确认」第 5 条，请技术方案作者确认后补入 4.3。

## 运行环境

- **Python 3.9 及以上**，只使用标准库（`argparse`/`json`/`re`/`os`/`tempfile`/`unicodedata`/`datetime`），**不需要 pip install 任何东西**。
- 本次实测环境：`Python 3.12.15`（Linux，`/usr/local/bin/python`，工作目录 `code/`）。代码未使用 3.10+ 专属语法，3.9 起应可用，但 3.9~3.11 未实测。
- 运行方式：**在 `code/` 目录下**执行 `python cli.py <子命令> [参数]`。（在仓库根目录执行 `python code/cli.py ...` 也可以，脚本所在目录会自动进入模块搜索路径。）
- 输出含中文，终端建议 UTF-8，例如 Windows 下 `chcp 65001`，Linux/macOS 默认即可。
- 三个子命令与文件对应关系：`cli.py`（argparse 入口：子命令、参数编排、异常 → 退出码）、`ledger.py`（核心逻辑：金额换算、校验、记录模型、JSON 读写、汇总与统计）。
- 说明：`docs/技术方案.md` 2.1 节把核心逻辑拆成一个 `ledger/` 包（`money.py`/`validators.py`/`model.py`/`store.py`/`reports.py`）。本实现按交付要求合并为**单文件 `ledger.py`**，内部仍按「金额 / 校验 / 模型 / 存储 / 报表」分小节，对外函数名与技术方案 4.2 的接口完全一致（`parse_amount_to_cents`、`format_cents`、`validate_*`、`load`、`append_record`、`monthly_summary`、`category_stats`、`render_summary`、`render_stats_table`），测试可按接口导入。

## 命令用法

统一口径（三个子命令共用一套校验函数）：

- 只支持人民币 **CNY**，不做多币种、不做汇率换算。
- **金额一律以「分」为单位的整数存储与计算，代码里不含浮点类型与浮点运算**：元→分按字符串正则解析（不做乘法换算），分→元只用整除与取余；显示时才换算成元并保留两位小数。解析 `35.80` → `amount_cents=3580`。
- 金额合法写法：`35`、`35.8`、`35.80`、`0.05`；整数部分 1~12 位、小数最多 2 位，上限 `999999999999.99` 元。非法：空、`0`、`0.00`、负数、`+`/`-` 号、`¥`、千分位逗号、空格、科学计数法、小数超 2 位（`1.005`）。
- 日期 `YYYY-MM-DD`（必须补零、且日期存在，`2024-02-29` 合法、`2026-02-30` 非法）；月份 `YYYY-MM`。
- 分类固定 8 类，只允许：**餐饮、交通、购物、居住、娱乐、医疗、教育、其他**（收入与支出共用同一套分类，不支持自定义）。
- `--data PATH` 是每个子命令自己的可选参数，写在子命令之后，默认 `./data/ledger.json`。

### add（记一笔）

```
python cli.py add --type {income,expense} --amount 元 --category 分类 [--date YYYY-MM-DD] [--note 文本] [--data PATH]
```

`--type`/`--amount`/`--category` 必填；`--note` 省略时存空字符串（不是 `null`）。**先全量校验、后写入**：任何校验失败都不改动数据文件。

`--date` 的两种「没给日期」状态行为不同（与 `--month ""` 必须报错的口径一致）：**完全不传 `--date`** 才用本机当天（本地时区）；**传了空串或纯空白**（例如脚本里 `--date "$D"` 而 `$D` 未赋值）按非法日期报错、退出码 2、不写入，**不静默改成今天**（返工修正项，见「验证状态」）。示例：

```
python cli.py add --type expense --amount 35.80 --category 餐饮 --date 2026-10-23
python cli.py add --type income --amount 1200 --category 其他 --date 2026-10-01 --note "项目款"
python cli.py add --type expense --amount 35.80 --category 餐饮 --date ""   # 空日期 → 报错退出码 2，不写入
```

### summary（按月汇总）

```
python cli.py summary --month YYYY-MM [--data PATH]
```

按 `YYYY-MM` 汇总收入、支出、结余（收入 − 支出，可为负）与笔数；不统计其他月份。只读命令，**数据文件不存在或该月无记录时按 0 条处理，退出码 0，且不会创建文件**。

### stats（按分类统计）

```
python cli.py stats --month YYYY-MM [--type {expense,income}] [--data PATH]
```

`--type` 省略时为 `expense`。只统计该月该类型；按**金额降序**排列，金额并列时按固定 8 类顺序；只列出有记录的分类；占比保留 1 位小数（四舍五入，不做补差），合计行固定 `100.0%`。该月该类型无记录时只输出一行提示，退出码 0，不输出表格。

### export（导出 CSV）：本期不做

按 CEO 指示（依据：`docs/PRD.md` v1.1 第 8 章、`docs/技术方案.md` ADR-6），**本期不提供 export 子命令、也不实现 CSV 生成**（编号保留不复用）。需要备份或带走数据时，直接复制数据文件 `data/ledger.json` 即可（它是可读 JSON 单文件）。将来恢复导出前需重新确认列定义、编码与转义口径。

### 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功（含空数据、无该月记录） |
| `2` | 参数或校验错误：非法分类、非法日期、非法月份、金额格式、非法 `--type`、缺必填参数 |
| `3` | 文件/IO 错误：数据文件损坏、`version` 未知、目录不可写 |
| `1` | 未预期错误（兜底） |

错误一律写到 **stderr**、单行、以「错误：」开头；校验失败时数据文件内容不变。

## 数据存储位置

- 默认路径：**`./data/ledger.json`**，相对**当前工作目录**；用 `--data PATH` 覆盖（写在子命令之后，例如 `add ... --data /tmp/t.json`）。注意：在哪个目录执行命令就在哪个目录找账本，换目录执行会看到不同的账本。
- 只有 `add` 会创建文件（目录不存在时自动创建，对应「表内无记录 → 首次写入」这一条迁移）；本次新建文件时会向 **stderr** 额外打一行 `（已新建数据文件：<绝对路径>）`——这行不是 stdout 输出契约，`add` 成功时 stdout 只有确认行。`summary`/`stats` 不创建文件。
- 写入方式：读入 → 追加 → 同目录临时文件 → `os.replace` 原子替换，中途中断不会留下半截 JSON。
- 文件格式（UTF-8 无 BOM、2 空格缩进）：`{"version":1,"records":[{"id":1,"type":"expense","amount_cents":3580,"category":"餐饮","date":"2026-10-23","note":"午餐","created_at":"2026-10-23T12:30:00"}]}`。金额只有 `amount_cents`（整数分）；`id` 从 1 起严格自增（`max(id)+1`）；文件被手工改坏或 `version` 不是 1 时，读取方会报错（退出码 3）且**绝不改写原文件**。
- 单用户单进程使用，不加写锁。备份/回滚：`cp data/ledger.json data/ledger.json.bak`，恢复时覆盖回来即可。
- 测试或试用请一律用 `--data` 指向临时文件，避免污染真实账本。

## 示例输出

以下命令均假设当前工作目录是 `code/`，并统一用 `--data /tmp/lt/t.json` 指向临时账本（真实使用时省略 `--data` 即写默认账本）。本节输出均为**真机实测结果**：第 1 轮原始记录见 `code/verify.log` 第 1 节（数据在 `/tmp/lt/`），返工回归（`--date` 空值修复）见第 4 节（数据在 `/tmp/r2b/`）。

记 5 笔（支出 35.80、收入 1200.00 带备注、精度用例 0.10 与 0.20、跨月的 35.8）：

```
$ python cli.py add --type expense --amount 35.80 --category 餐饮 --date 2026-10-23 --data /tmp/lt/t.json
已记录 #1：支出 35.80 元 餐饮 2026-10-23

$ python cli.py add --type income --amount 1200 --category 其他 --date 2026-10-01 --note "项目款" --data /tmp/lt/t.json
已记录 #2：收入 1200.00 元 其他 2026-10-01（项目款）

$ python cli.py add --type expense --amount 0.10 --category 交通 --date 2026-10-24 --data /tmp/lt/t.json
已记录 #3：支出 0.10 元 交通 2026-10-24

$ python cli.py add --type expense --amount 0.20 --category 交通 --date 2026-10-24 --data /tmp/lt/t.json
已记录 #4：支出 0.20 元 交通 2026-10-24

$ python cli.py add --type expense --amount 35.8 --category 餐饮 --date 2026-09-28 --data /tmp/lt/t.json
已记录 #5：支出 35.80 元 餐饮 2026-09-28
```

（首个 `add` 时文件尚不存在，stderr 另有一行 `（已新建数据文件：/tmp/lt/t.json）`；stdout 始终只有上面这一行确认行。注意 `35.8` 与 `35.80` 等价，落盘的都是 `amount_cents=3580`。）

按月汇总：

```
$ python cli.py summary --month 2026-10 --data /tmp/lt/t.json
2026-10 汇总（人民币 CNY）
收入：1 笔   1200.00 元
支出：3 笔   36.10 元
结余：1163.90 元
```

按分类统计（支出）：

```
$ python cli.py stats --month 2026-10 --type expense --data /tmp/lt/t.json
2026-10 支出分类统计（共 36.10 元 / 3 笔）
分类     笔数   金额(元)     占比
餐饮        1      35.80    99.2%
交通        2       0.30     0.8%
合计        3      36.10   100.0%

$ python cli.py stats --month 2026-10 --type income --data /tmp/lt/t.json
2026-10 收入分类统计（共 1200.00 元 / 1 笔）
分类     笔数   金额(元)     占比
其他        1    1200.00   100.0%
合计        1    1200.00   100.0%
```

这组输出就是金额精度的卡口：`0.10` 与 `0.20` 两笔支出显示为交通 `2 笔 0.30 元`（整数分 10 + 20 = 30），不会出现 `0.30000000000000004`。

12 笔支出共 3456.78 元（餐饮 5 笔 1200.00、交通 4 笔 900.00、购物 3 笔 1356.78）时，行序按金额降序：

```
$ python cli.py stats --month 2026-10 --type expense --data /tmp/lt/big.json
2026-10 支出分类统计（共 3456.78 元 / 12 笔）
分类     笔数   金额(元)     占比
购物        3    1356.78    39.2%
餐饮        5    1200.00    34.7%
交通        4     900.00    26.0%
合计       12    3456.78   100.0%
```

空数据（数据文件不存在或该月无记录，退出码 0，不创建文件）：

```
$ python cli.py summary --month 2026-11 --data /tmp/lt/t.json
收入：0 笔 0.00 元
支出：0 笔 0.00 元
结余：0.00 元

$ python cli.py stats --month 2026-11 --data /tmp/lt/t.json
该月没有支出记录
```

非法输入（均不写入数据，stderr 输出、退出码 2）：

```
$ python cli.py add --type expense --amount 1.005 --category 餐饮 --data /tmp/lt/t.json
错误：金额必须是正数（单位：元），最多两位小数，例如 35.80

$ python cli.py add --type expense --amount 10 --category 旅行 --data /tmp/lt/t.json
错误：分类必须是以下 8 类之一：餐饮、交通、购物、居住、娱乐、医疗、教育、其他

$ python cli.py add --type expense --amount 10 --category 餐饮 --date 2026-02-30 --data /tmp/lt/t.json
错误：日期不存在或格式应为 YYYY-MM-DD（如 2026-10-23）

$ python cli.py add --type expense --amount 10 --category 餐饮 --date "" --data /tmp/r2b/t.json
错误：日期不存在或格式应为 YYYY-MM-DD（如 2026-10-23）        # 返工用例：显式空日期不再静默写成本机当天；退出码 2，数据文件 sha256 前后一致（见 code/verify.log 4-E/4-F）

$ python cli.py summary --month 2026-13 --data /tmp/lt/t.json
错误：月份格式应为 YYYY-MM（如 2026-10）

$ python cli.py stats --month 2026-10 --type transfer --data /tmp/lt/t.json
错误：argument --type: invalid choice: 'transfer' (choose from expense, income)   # 实测原文（本机 Python 3.12 不带引号），stderr 前还有一行 argparse 的 usage
```

## 验证状态（真机实测，2026-10-23）

第 1 轮与返工后的第 2 轮都在能运行 Python 的机器上实际执行过（解释器 `Python 3.12.15`，cwd = `code/`，第 1 轮数据在 `/tmp/lt/`、第 2 轮在 `/tmp/r2b/`；未创建也未改动 `code/data/`），整理后的原始记录见 `code/verify.log` 第 1 节与第 4 节。第 3 轮（本次返工只改文档）又在 `/tmp/rev3/` 复跑了一遍：本文每个代码块与实测 stdout **逐字节一致**（`ALL: PASS`），`--date` 空值与零浮点卡口复跑通过，代码零改动（见 `code/verify.log` 第 5 节）。**已实测通过**的部分：

- `python -m py_compile ledger.py cli.py` 通过；`python cli.py --help` 正常列出 `{add,summary,stats}`。
- 主流程：上面 5 笔 `add`、两个月的 `summary`、两个类型的 `stats` 全部按契约输出（退出码 0）。
- 数据文件核对：`amount_cents` 实测为 `int`，值为 `[3580, 120000, 10, 20, 3580]`；未传 `--note` 存 `""`（不是 `null`）；`id` 为 1..N 严格自增且原记录未被改写。
- 金额精度：`0.10` + `0.20` 两笔支出在 `stats` 显示交通 `2 笔 0.30 元`；2026-10 支出合计按整数分复算 = 3610 分 = 36.10 元。
- 非法输入均退出码 2 且数据文件字节未变（sha256 前后一致）：分类 `旅行`、日期 `2026-02-30` / `2026-2-3` / `2026/10/23`、月份 `2026-13` / `2026-1` / `202610`、`--type transfer`（`add` 与 `stats` 各一次）；闰年 `2024-02-29` 写入成功。
- **`--date` 空值（返工修复项）**：`--date ""` 与 `--date "   "` 均退出码 2、stderr 单行报「日期不存在或格式应为 YYYY-MM-DD（如 2026-10-23）」，数据文件 sha256 前后均为 `eb65482075f8…`（未变）；与 `summary --month ""` 退出码 2 的口径一致。完全不带 `--date` 时退出码 0，落盘 `date` = 本机当天（实测 `2026-10-23`）。
- 只读命令无副作用：数据文件不存在时 `summary` 输出三行全 0、`stats` 输出「该月没有支出记录」，均退出码 0，且事后确认文件**仍不存在**。
- 数据文件损坏（内容为 `{`）：`summary` / `stats` / `add` 均退出码 3、stderr 单行以「错误：」开头，原文件未被改写。
- 零浮点卡口（`docs/技术方案.md` 2.3 的两条 grep，范围 `code/ledger.py code/cli.py`）：**零命中**。
- 性能（1 万条 / 1.92 MB，端到端含解释器启动）：`summary` 0.23 秒、`stats` 0.24 秒、`add` 0.33 秒，满足 PRD「单条命令 < 1 秒」。

- **第 1 轮未实测的 4 项已在第 2 轮全部补跑通过**（原始记录：`code/verify.log` 4-I / 4-J / 4-K / 4-L）：① 非法金额（`1.005`、`0`、`0.00`、`-5`、`abc`、`1,200`、` 35.80`、`¥35.80`、`1e3`、空串）全部退出码 2、报金额错误、目标文件未被创建；② 金额边界 `0.05` → `amount_cents=5`、`999999999999.99` → `99999999999999`、整数部分 13 位 → 退出码 2；③ 在空目录不带 `--data` 执行 `add` → 生成 `./data/ledger.json`（stderr 提示绝对路径）；④ 从仓库根目录 `python code/cli.py summary --month 2026-10 --data …` → 退出码 0、输出与在 `code/` 下逐字一致。返工回归后主流程输出与第 1 轮逐字一致（无回归），零浮点三条 grep 仍零命中。
- **仍未实测**：Python 3.9~3.11 的兼容性（只实测过 3.12.15；代码未用 3.10+ 语法）。

## 待确认（实现侧已做假设，需 CEO 或审查确认）

1. **文件布局**：按交付要求合并为单文件 `code/ledger.py`，与技术方案 2.1 的 `ledger/` 包布局不同（接口名保持一致）。若必须按包拆分，请答复后调整。
2. **空数据的 summary 输出**：只输出文档列出的三行（`收入：0 笔 0.00 元` / `支出：0 笔 0.00 元` / `结余：0.00 元`），不带标题行 `2026-10 汇总（人民币 CNY）`——依据是 PRD 故事 2 与 技术方案 4.4 对该场景逐条列出了这三行。若希望空数据也带标题行，请告知。
3. **`--note` 是否保留为 P0、是否有长度上限**：当前不截断、不限制长度（PRD 第 7 章两处均标注「需要 CEO 决定」）。
4. **`stats` 列宽**：数值/列名/列顺序/行序按契约实现；列宽（分类 6、笔数 6、金额 10、占比 8）是排版细节，测试请对空白做弹性匹配（与 技术方案 4.5 的口径一致）。
5. **`--date` 显式空值的口径未写入技术方案 v1.2**：本文规定「完全不传 `--date` → 本机当天；传空串或纯空白 → 非法日期报错、退出码 2、不写入」。技术方案 v1.2 的 4.3 只规定了前者（`--date` 默认值「本机今天」），没有显式空值这一行——它是本轮按测试审查意见实现并实测的（见「验证状态」与 `code/verify.log` 4-E/4-F），不是技术方案的原文。请技术方案作者确认后在 4.3 的 `--date` 行补一句，使文档与本实现口径一致。
