## 技术交付物

### 反馈分析仪表盘

```python
from dataclasses import dataclass, field
from collections import Counter
from datetime import datetime
from enum import Enum
from typing import List, Optional


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(Enum):
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    UX_ISSUE = "ux_issue"
    PERFORMANCE = "performance"
    PRAISE = "praise"


@dataclass
class Feedback:
    id: str
    source: str  # appstore / zendesk / social / survey
    content: str
    category: Category
    severity: Severity
    sentiment: float  # -1.0 到 1.0
    user_tier: str  # free / pro / enterprise
    created_at: datetime
    tags: List[str] = field(default_factory=list)


class FeedbackAnalyzer:
    """用户反馈分析器"""

    def __init__(self, feedbacks: List[Feedback]):
        self.feedbacks = feedbacks

    def top_issues(self, n: int = 10) -> list:
        """按标签统计 Top N 问题"""
        tag_counts = Counter()
        for fb in self.feedbacks:
            if fb.category != Category.PRAISE:
                for tag in fb.tags:
                    tag_counts[tag] += 1
        return tag_counts.most_common(n)

    def severity_distribution(self) -> dict:
        """严重程度分布"""
        dist = Counter(fb.severity.value for fb in self.feedbacks)
        total = len(self.feedbacks)
        return {k: {"count": v, "pct": f"{v/total:.1%}"}
                for k, v in dist.items()}

    def sentiment_by_tier(self) -> dict:
        """各用户层级的情感得分"""
        tier_scores = {}
        for fb in self.feedbacks:
            tier_scores.setdefault(fb.tier, []).append(fb.sentiment)
        return {tier: sum(s)/len(s)
                for tier, s in tier_scores.items()}

    def weekly_report(self) -> str:
        """生成周报摘要"""
        total = len(self.feedbacks)
        top = self.top_issues(5)
        critical = sum(
            1 for fb in self.feedbacks
            if fb.severity == Severity.CRITICAL
        )
        return (
            f"本周收到 {total} 条反馈，"
            f"其中 {critical} 条严重问题。\n"
            f"Top 5 问题：{', '.join(t[0] for t in top)}"
        )
```
