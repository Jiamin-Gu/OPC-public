## 技术交付物

### 身份解析 Schema

每次解析调用应返回如下结构：

```json
{
  "entity_id": "a1b2c3d4-...",
  "confidence": 0.94,
  "is_new": false,
  "canonical_data": {
    "email": "wsmith@acme.com",
    "first_name": "William",
    "last_name": "Smith",
    "phone": "+15550142"
  },
  "version": 7
}
```

引擎通过昵称归一化将"Bill"匹配到"William"。电话号码归一化为 E.164 格式。置信度 0.94，基于邮箱精确匹配 + 姓名模糊匹配 + 电话匹配。

### 合并提案结构

提出合并时，始终附带逐字段证据：

```json
{
  "entity_a_id": "a1b2c3d4-...",
  "entity_b_id": "e5f6g7h8-...",
  "confidence": 0.87,
  "evidence": {
    "email_match": { "score": 1.0, "values": ["wsmith@acme.com", "wsmith@acme.com"] },
    "name_match": { "score": 0.82, "values": ["William Smith", "Bill Smith"] },
    "phone_match": { "score": 1.0, "values": ["+15550142", "+15550142"] },
    "reasoning": "邮箱和电话相同。姓名不同，但'Bill'是'William'的常见昵称。"
  }
}
```

其他智能体可以在执行前审核此提案。

### 决策表：直接变更 vs. 提案

| 场景 | 操作 | 原因 |
|------|------|------|
| 单智能体，高置信度 (>0.95) | 直接合并 | 无歧义，无需咨询其他智能体 |
| 多智能体，中等置信度 | 提出合并提案 | 让其他智能体审核证据 |
| 智能体不同意之前的合并 | 带 member_ids 提出拆分提案 | 不要直接撤销——提出提案让其他人验证 |
| 修正数据字段 | 带 expected_version 直接变更 | 字段更新不需要多智能体审核 |
| 对匹配不确定 | 先模拟，再决定 | 预览结果而不提交 |

### 匹配技术

```python
class IdentityMatcher:
    """
    身份解析的核心匹配逻辑。
    逐字段对比两条记录，使用类型感知评分。
    """

    def score_pair(self, record_a: dict, record_b: dict, rules: list) -> float:
        total_weight = 0.0
        weighted_score = 0.0

        for rule in rules:
            field = rule["field"]
            val_a = record_a.get(field)
            val_b = record_b.get(field)

            if val_a is None or val_b is None:
                continue

            # 对比前先归一化
            val_a = self.normalize(val_a, rule.get("normalizer", "generic"))
            val_b = self.normalize(val_b, rule.get("normalizer", "generic"))

            # 使用指定方法对比
            score = self.compare(val_a, val_b, rule.get("comparator", "exact"))
            weighted_score += score * rule["weight"]
            total_weight += rule["weight"]

        return weighted_score / total_weight if total_weight > 0 else 0.0

    def normalize(self, value: str, normalizer: str) -> str:
        if normalizer == "email":
            return value.lower().strip()
        elif normalizer == "phone":
            return re.sub(r"[^\d+]", "", value)  # 只保留数字
        elif normalizer == "name":
            return self.expand_nicknames(value.lower().strip())
        return value.lower().strip()

    def expand_nicknames(self, name: str) -> str:
        nicknames = {
            "bill": "william", "bob": "robert", "jim": "james",
            "mike": "michael", "dave": "david", "joe": "joseph",
            "tom": "thomas", "dick": "richard", "jack": "john",
        }
        return nicknames.get(name, name)
```
