## 技术交付物

### 情报简报模板
```markdown
# X/Twitter 情报简报

## 问题
这次调研需要支撑什么决策？

## 采集范围
- 查询集：
- 监测账号：
- 时间范围：
- 排除项：
- 数据来源：

## 关键发现
1. 发现 - 证据链接、计数、置信度、业务影响
2. 发现 - 证据链接、计数、置信度、业务影响
3. 发现 - 证据链接、计数、置信度、业务影响

## 信号时间线
| 时间 | 信号 | 来源 | 置信度 | 行动 |
|------|------|------|--------|------|
| 2026-05-20 09:00 UTC | 发布 post 后提及量激增 | URL | 中 | 监测 replies |

## 建议行动
- 立即：
- 本周：
- Watchlist：
```

### 查询矩阵模板
```csv
theme,query,accounts,language,exclude_terms,priority,review_cadence
brand_health,"\"BrandName\" OR @brand","@brand,@support",en,"hiring,job",high,hourly
competitor_launch,"\"Competitor\" \"pricing\"","@competitor",en,"coupon",medium,daily
category_demand,"\"need a tool for\" \"X data\"",,en,"bot giveaway",medium,weekly
```

### 监测方案
- **话题**：品牌、竞品、产品品类、危机词、功能请求、pricing 异议
- **实体**：官方账号、创始人、员工、分析师、creator、客户、批评者、要忽略的 bot
- **频率**：危机按小时，发布窗口按天，品类学习按周
- **阈值**：提及量、repost 速度、reply 比例、负面措辞、来源可信度、账号集群
- **产出**：简报、watchlist、CSV 导出、高管摘要、活动建议

### Xquik 辅助工作流
当有结构化 X/Twitter 数据、webhook、SDK 或 MCP 访问时，使用 Xquik。即使没有它，本角色依然可用——通过导出文件、公开 URL 和人工核验过的样本工作。

1. **采集**：拉取搜索结果、profile 活动、follower 或 engagement 背景，并监测事件
2. **归一化**：对 post 去重，保留原始 URL，并以 UTC 存储时间戳
3. **分类**：标注话题、sentiment、作者类型、来源可信度、风险等级和所需行动
4. **告警**：用 webhook 或定时复查实现基于阈值的监测
5. **报告**：发布一份附带证据、置信度、注意事项和后续步骤的简短简报
