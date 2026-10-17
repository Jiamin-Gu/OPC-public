## 技术交付物

### 文件监控

- 用文件系统监听器监控目录中的 `.xlsx` 和 `.xls` 文件
- 忽略 Excel 的临时锁文件（`~$` 开头的）
- 等文件写入完成后再处理（检测文件大小稳定后再开始）
- 支持嵌套子目录扫描，按区域/团队组织文件

### 指标提取

- 解析工作簿中的所有 sheet
- 灵活映射列名：`revenue/sales/total_sales`、`units/qty/quantity` 等
- 当配额和收入都有时自动计算达成率
- 处理数字字段中的货币格式（$、¥、€、逗号、空格分隔符）
- 识别并跳过合计行、空白行和注释行

### 数据持久化

- 提取的指标批量插入 PostgreSQL
- 用事务保证原子性
- 每行指标都记录来源文件，方便审计追溯

### 代码示例：列名模糊匹配

```python
import re
from difflib import SequenceMatcher

# 列名标准化映射
COLUMN_ALIASES = {
    "revenue": ["revenue", "sales", "total_sales", "net_revenue", "销售额", "营收"],
    "units": ["units", "qty", "quantity", "units_sold", "销量", "数量"],
    "quota": ["quota", "target", "goal", "plan", "配额", "目标"],
    "rep_name": ["rep", "name", "sales_rep", "account_exec", "销售代表", "姓名"],
    "rep_email": ["email", "mail", "rep_email", "邮箱"],
}

def fuzzy_match_column(header: str, threshold: float = 0.75) -> str | None:
    """将实际列名模糊匹配到标准字段名"""
    normalized = re.sub(r'[\s_\-]+', '_', header.strip().lower())
    for standard, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            ratio = SequenceMatcher(None, normalized, alias).ratio()
            if ratio >= threshold or normalized.startswith(alias):
                return standard
    return None

def detect_metric_type(sheet_name: str) -> str:
    """从 sheet 名称推断指标类型"""
    name = sheet_name.upper().strip()
    if any(k in name for k in ["MTD", "月", "MONTHLY", "当月"]):
        return "MTD"
    elif any(k in name for k in ["YTD", "年累计", "YEAR TO DATE"]):
        return "YTD"
    elif any(k in name for k in ["FORECAST", "预测", "YEAR END", "年末"]):
        return "FORECAST"
    return "MTD"  # 安全默认值
```

### 代码示例：幂等导入

```python
import hashlib

def file_content_hash(filepath: str) -> str:
    """计算文件内容哈希用于去重"""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def import_with_dedup(filepath: str, db_conn):
    """幂等导入：同一文件不会重复处理"""
    content_hash = file_content_hash(filepath)
    existing = db_conn.execute(
        "SELECT id FROM import_log WHERE file_hash = %s AND status = 'completed'",
        (content_hash,)
    ).fetchone()
    if existing:
        logger.info(f"跳过已导入文件: {filepath} (hash={content_hash[:12]})")
        return {"status": "skipped", "reason": "duplicate"}
    # 开始事务性导入...
```
