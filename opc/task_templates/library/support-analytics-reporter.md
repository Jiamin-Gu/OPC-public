## 你的分析交付物

### 高管仪表盘模板
```sql
-- 关键业务指标仪表盘
WITH monthly_metrics AS (
  SELECT
    DATE_TRUNC('month', date) as month,
    SUM(revenue) as monthly_revenue,
    COUNT(DISTINCT customer_id) as active_customers,
    AVG(order_value) as avg_order_value,
    SUM(revenue) / COUNT(DISTINCT customer_id) as revenue_per_customer
  FROM transactions
  WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH)
  GROUP BY DATE_TRUNC('month', date)
),
growth_calculations AS (
  SELECT *,
    LAG(monthly_revenue, 1) OVER (ORDER BY month) as prev_month_revenue,
    (monthly_revenue - LAG(monthly_revenue, 1) OVER (ORDER BY month)) /
     LAG(monthly_revenue, 1) OVER (ORDER BY month) * 100 as revenue_growth_rate
  FROM monthly_metrics
)
SELECT
  month,
  monthly_revenue,
  active_customers,
  avg_order_value,
  revenue_per_customer,
  revenue_growth_rate,
  CASE
    WHEN revenue_growth_rate > 10 THEN 'High Growth'
    WHEN revenue_growth_rate > 0 THEN 'Positive Growth'
    ELSE 'Needs Attention'
  END as growth_status
FROM growth_calculations
ORDER BY month DESC;
```

### 客户细分分析
```python
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
import matplotlib.pyplot as plt
import seaborn as sns

# 客户终身价值与细分
def customer_segmentation_analysis(df):
    """
    执行 RFM 分析和客户细分
    """
    # 计算 RFM 指标
    current_date = df['date'].max()
    rfm = df.groupby('customer_id').agg({
        'date': lambda x: (current_date - x.max()).days,  # 最近一次消费（Recency）
        'order_id': 'count',                               # 消费频率（Frequency）
        'revenue': 'sum'                                   # 消费金额（Monetary）
    }).rename(columns={
        'date': 'recency',
        'order_id': 'frequency',
        'revenue': 'monetary'
    })

    # 创建 RFM 评分
    rfm['r_score'] = pd.qcut(rfm['recency'], 5, labels=[5,4,3,2,1])
    rfm['f_score'] = pd.qcut(rfm['frequency'].rank(method='first'), 5, labels=[1,2,3,4,5])
    rfm['m_score'] = pd.qcut(rfm['monetary'], 5, labels=[1,2,3,4,5])

    # 客户分群
    rfm['rfm_score'] = rfm['r_score'].astype(str) + rfm['f_score'].astype(str) + rfm['m_score'].astype(str)

    def segment_customers(row):
        if row['rfm_score'] in ['555', '554', '544', '545', '454', '455', '445']:
            return 'Champions'
        elif row['rfm_score'] in ['543', '444', '435', '355', '354', '345', '344', '335']:
            return 'Loyal Customers'
        elif row['rfm_score'] in ['553', '551', '552', '541', '542', '533', '532', '531', '452', '451']:
            return 'Potential Loyalists'
        elif row['rfm_score'] in ['512', '511', '422', '421', '412', '411', '311']:
            return 'New Customers'
        elif row['rfm_score'] in ['155', '154', '144', '214', '215', '115', '114']:
            return 'At Risk'
        elif row['rfm_score'] in ['155', '154', '144', '214', '215', '115', '114']:
            return 'Cannot Lose Them'
        else:
            return 'Others'

    rfm['segment'] = rfm.apply(segment_customers, axis=1)

    return rfm

# 生成洞察和建议
def generate_customer_insights(rfm_df):
    insights = {
        'total_customers': len(rfm_df),
        'segment_distribution': rfm_df['segment'].value_counts(),
        'avg_clv_by_segment': rfm_df.groupby('segment')['monetary'].mean(),
        'recommendations': {
            'Champions': '奖励忠诚度，请求推荐，追加销售高端产品',
            'Loyal Customers': '维护关系，推荐新产品，忠诚度计划',
            'At Risk': '重新激活活动，特别优惠，挽回策略',
            'New Customers': '优化入门体验，早期互动，产品教育'
        }
    }
    return insights
```

### 营销效果仪表盘
```javascript
// 营销归因与 ROI 分析
const marketingDashboard = {
  // 多触点归因模型
  attributionAnalysis: `
    WITH customer_touchpoints AS (
      SELECT
        customer_id,
        channel,
        campaign,
        touchpoint_date,
        conversion_date,
        revenue,
        ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY touchpoint_date) as touch_sequence,
        COUNT(*) OVER (PARTITION BY customer_id) as total_touches
      FROM marketing_touchpoints mt
      JOIN conversions c ON mt.customer_id = c.customer_id
      WHERE touchpoint_date <= conversion_date
    ),
    attribution_weights AS (
      SELECT *,
        CASE
          WHEN touch_sequence = 1 AND total_touches = 1 THEN 1.0  -- 单触点
          WHEN touch_sequence = 1 THEN 0.4                       -- 首次触点
          WHEN touch_sequence = total_touches THEN 0.4           -- 最后触点
          ELSE 0.2 / (total_touches - 2)                        -- 中间触点
        END as attribution_weight
      FROM customer_touchpoints
    )
    SELECT
      channel,
      campaign,
      SUM(revenue * attribution_weight) as attributed_revenue,
      COUNT(DISTINCT customer_id) as attributed_conversions,
      SUM(revenue * attribution_weight) / COUNT(DISTINCT customer_id) as revenue_per_conversion
    FROM attribution_weights
    GROUP BY channel, campaign
    ORDER BY attributed_revenue DESC;
  `,

  // 营销活动 ROI 计算
  campaignROI: `
    SELECT
      campaign_name,
      SUM(spend) as total_spend,
      SUM(attributed_revenue) as total_revenue,
      (SUM(attributed_revenue) - SUM(spend)) / SUM(spend) * 100 as roi_percentage,
      SUM(attributed_revenue) / SUM(spend) as revenue_multiple,
      COUNT(conversions) as total_conversions,
      SUM(spend) / COUNT(conversions) as cost_per_conversion
    FROM campaign_performance
    WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
    GROUP BY campaign_name
    HAVING SUM(spend) > 1000  -- 过滤有效投放
    ORDER BY roi_percentage DESC;
  `
};
```

## 你的分析报告模板

```markdown
# [分析名称] - 商业智能报告

## 高管摘要

### 关键发现
**核心洞察**：[最重要的业务洞察及量化影响]
**辅助洞察**：[2-3 个有数据支撑的辅助洞察]
**统计置信度**：[置信水平和样本量验证]
**业务影响**：[对收入、成本或效率的量化影响]

### 需要立即采取的行动
1. **高优先级**：[行动方案及预期影响和时间线]
2. **中优先级**：[行动方案及成本效益分析]
3. **长期**：[战略建议及衡量计划]

## 详细分析

### 数据基础
**数据来源**：[数据来源列表及质量评估]
**样本量**：[记录数量及统计功效分析]
**时间范围**：[分析时段及季节性考量]
**数据质量评分**：[完整性、准确性和一致性指标]

### 统计分析
**方法论**：[统计方法及其理由]
**假设检验**：[零假设和备择假设及结果]
**置信区间**：[关键指标的 95% 置信区间]
**效应量**：[实际显著性评估]

### 业务指标
**当前表现**：[基线指标及趋势分析]
**表现驱动因素**：[影响结果的关键因素]
**基准对比**：[行业或内部基准]
**改善机会**：[量化的改善潜力]

## 建议

### 战略建议
**建议 1**：[行动方案及 ROI 预测和实施计划]
**建议 2**：[举措及资源需求和时间线]
**建议 3**：[流程改进及效率提升]

### 实施路线图
**第一阶段（30 天）**：[立即行动及成功指标]
**第二阶段（90 天）**：[中期举措及衡量计划]
**第三阶段（6 个月）**：[长期战略变革及评估标准]

### 成功衡量
**主要 KPI**：[关键绩效指标及目标值]
**辅助指标**：[支持性指标及基准]
**监控频率**：[审查计划和报告节奏]
**仪表盘链接**：[实时监控仪表盘的访问链接]

---
**数据分析师**：[你的名字]
**分析日期**：[日期]
**下次评审**：[计划的跟进日期]
**利益相关者签字**：[审批流程状态]
```
