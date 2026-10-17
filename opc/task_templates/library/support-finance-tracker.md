## 财务管理交付物

### 综合预算框架
```sql
-- 年度预算与季度差异分析
WITH budget_actuals AS (
  SELECT
    department,
    category,
    budget_amount,
    actual_amount,
    DATE_TRUNC('quarter', date) as quarter,
    budget_amount - actual_amount as variance,
    (actual_amount - budget_amount) / budget_amount * 100 as variance_percentage
  FROM financial_data
  WHERE fiscal_year = YEAR(CURRENT_DATE())
),
department_summary AS (
  SELECT
    department,
    quarter,
    SUM(budget_amount) as total_budget,
    SUM(actual_amount) as total_actual,
    SUM(variance) as total_variance,
    AVG(variance_percentage) as avg_variance_pct
  FROM budget_actuals
  GROUP BY department, quarter
)
SELECT
  department,
  quarter,
  total_budget,
  total_actual,
  total_variance,
  avg_variance_pct,
  CASE
    WHEN ABS(avg_variance_pct) <= 5 THEN 'On Track'       -- 在轨
    WHEN avg_variance_pct > 5 THEN 'Over Budget'           -- 超预算
    ELSE 'Under Budget'                                     -- 低于预算
  END as budget_status,
  total_budget - total_actual as remaining_budget            -- 剩余预算
FROM department_summary
ORDER BY department, quarter;
```

### 现金流管理系统
```python
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt

class CashFlowManager:
    def __init__(self, historical_data):
        self.data = historical_data
        self.current_cash = self.get_current_cash_position()

    def forecast_cash_flow(self, periods=12):
        """
        生成 12 个月滚动现金流预测
        """
        forecast = pd.DataFrame()

        # 历史模式分析
        monthly_patterns = self.data.groupby('month').agg({
            'receipts': ['mean', 'std'],
            'payments': ['mean', 'std'],
            'net_cash_flow': ['mean', 'std']
        }).round(2)

        # 带季节性因子的预测
        for i in range(periods):
            forecast_date = datetime.now() + timedelta(days=30*i)
            month = forecast_date.month

            # 计算季节性系数
            seasonal_factor = self.calculate_seasonal_factor(month)

            forecasted_receipts = (monthly_patterns.loc[month, ('receipts', 'mean')] *
                                 seasonal_factor * self.get_growth_factor())
            forecasted_payments = (monthly_patterns.loc[month, ('payments', 'mean')] *
                                 seasonal_factor)

            net_flow = forecasted_receipts - forecasted_payments

            forecast = forecast.append({
                'date': forecast_date,
                'forecasted_receipts': forecasted_receipts,      # 预计收款
                'forecasted_payments': forecasted_payments,      # 预计付款
                'net_cash_flow': net_flow,                       # 净现金流
                'cumulative_cash': self.current_cash + forecast['net_cash_flow'].sum() if len(forecast) > 0 else self.current_cash + net_flow,  # 累计现金
                'confidence_interval_low': net_flow * 0.85,      # 置信区间下限
                'confidence_interval_high': net_flow * 1.15      # 置信区间上限
            }, ignore_index=True)

        return forecast

    def identify_cash_flow_risks(self, forecast_df):
        """
        识别潜在的现金流风险和机会
        """
        risks = []
        opportunities = []

        # 现金余额过低预警
        low_cash_periods = forecast_df[forecast_df['cumulative_cash'] < 50000]
        if not low_cash_periods.empty:
            risks.append({
                'type': '现金余额过低预警',
                'dates': low_cash_periods['date'].tolist(),
                'minimum_cash': low_cash_periods['cumulative_cash'].min(),
                'action_required': '加快应收账款回收或延迟应付账款'
            })

        # 闲置资金投资机会
        high_cash_periods = forecast_df[forecast_df['cumulative_cash'] > 200000]
        if not high_cash_periods.empty:
            opportunities.append({
                'type': '投资机会',
                'excess_cash': high_cash_periods['cumulative_cash'].max() - 100000,
                'recommendation': '考虑短期理财或提前支付以获取折扣'
            })

        return {'risks': risks, 'opportunities': opportunities}

    def optimize_payment_timing(self, payment_schedule):
        """
        优化付款时间安排，改善现金流
        """
        optimized_schedule = payment_schedule.copy()

        # 按提前付款折扣的年化收益率排优先级
        optimized_schedule['priority_score'] = (
            optimized_schedule['early_pay_discount'] *
            optimized_schedule['amount'] * 365 /
            optimized_schedule['payment_terms']
        )

        # 安排付款顺序：优先拿折扣，同时保证现金流安全
        optimized_schedule = optimized_schedule.sort_values('priority_score', ascending=False)

        return optimized_schedule
```

### 投资分析框架
```python
class InvestmentAnalyzer:
    def __init__(self, discount_rate=0.10):
        self.discount_rate = discount_rate

    def calculate_npv(self, cash_flows, initial_investment):
        """
        计算净现值（NPV），用于投资决策
        """
        npv = -initial_investment
        for i, cf in enumerate(cash_flows):
            npv += cf / ((1 + self.discount_rate) ** (i + 1))
        return npv

    def calculate_irr(self, cash_flows, initial_investment):
        """
        计算内部收益率（IRR）
        """
        from scipy.optimize import fsolve

        def npv_function(rate):
            return sum([cf / ((1 + rate) ** (i + 1)) for i, cf in enumerate(cash_flows)]) - initial_investment

        try:
            irr = fsolve(npv_function, 0.1)[0]
            return irr
        except:
            return None

    def payback_period(self, cash_flows, initial_investment):
        """
        计算投资回收期（年）
        """
        cumulative_cf = 0
        for i, cf in enumerate(cash_flows):
            cumulative_cf += cf
            if cumulative_cf >= initial_investment:
                return i + 1 - ((cumulative_cf - initial_investment) / cf)
        return None

    def investment_analysis_report(self, project_name, initial_investment, annual_cash_flows, project_life):
        """
        生成完整的投资分析报告
        """
        npv = self.calculate_npv(annual_cash_flows, initial_investment)
        irr = self.calculate_irr(annual_cash_flows, initial_investment)
        payback = self.payback_period(annual_cash_flows, initial_investment)
        roi = (sum(annual_cash_flows) - initial_investment) / initial_investment * 100

        # 风险评估
        risk_score = self.assess_investment_risk(annual_cash_flows, project_life)

        return {
            'project_name': project_name,
            'initial_investment': initial_investment,
            'npv': npv,
            'irr': irr * 100 if irr else None,
            'payback_period': payback,
            'roi_percentage': roi,
            'risk_score': risk_score,
            'recommendation': self.get_investment_recommendation(npv, irr, payback, risk_score)
        }

    def get_investment_recommendation(self, npv, irr, payback, risk_score):
        """
        根据分析结果生成投资建议
        """
        if npv > 0 and irr and irr > self.discount_rate and payback and payback < 3:
            if risk_score < 3:
                return "强烈建议投资 - 回报优秀且风险可控"
            else:
                return "建议投资 - 回报不错但需要持续关注风险"
        elif npv > 0 and irr and irr > self.discount_rate:
            return "有条件投资 - 回报为正，建议和其他方案对比后决定"
        else:
            return "不建议投资 - 回报不足以覆盖投入"
```

## 财务报告模板

```markdown
# [期间] 财务绩效报告

## 摘要

### 核心财务指标
**营收**：$[金额]（预算偏差 [+/-]%，同比 [+/-]%）
**运营费用**：$[金额]（预算偏差 [+/-]%）
**净利润**：$[金额]（利润率：[%]，预算偏差：[+/-]%）
**现金余额**：$[金额]（变动 [+/-]%，可覆盖 [天] 运营支出）

### 关键财务信号
**预算偏差**：[重大偏差及原因说明]
**现金流状况**：[经营、投资、融资现金流]
**核心比率**：[流动性、盈利能力、运营效率比率]
**风险因素**：[需要关注的财务风险]

### 待办事项
1. **紧急**：[行动、财务影响和时间线]
2. **短期**：[30 天内的举措，附成本效益分析]
3. **战略**：[长期财务规划建议]

## 详细财务分析

### 营收表现
**收入结构**：[按产品/服务拆分，附增长分析]
**客户分析**：[收入集中度和客户终身价值]
**市场表现**：[市场份额和竞争地位的影响]
**季节性**：[季节性规律和预测调整]

### 成本结构分析
**费用分类**：[固定 vs. 可变成本，附优化空间]
**部门绩效**：[成本中心分析和效率指标]
**供应商管理**：[主要供应商费用和谈判空间]
**成本趋势**：[费用走势和通胀影响分析]

### 现金流管理
**经营现金流**：$[金额]（质量评分：[等级]）
**营运资金**：[应收账款天数、存货周转率、付款账期]
**资本开支**：[投资优先级和 ROI 分析]
**融资活动**：[偿债、股权变动、分红政策]

## 预算 vs. 实际分析

### 差异分析
**有利差异**：[正向偏差及原因]
**不利差异**：[负向偏差及纠正措施]
**预测调整**：[基于实际表现的预测更新]
**预算调剂**：[建议的预算调整]

### 部门绩效
**表现优秀**：[超额完成预算目标的部门]
**需要关注**：[偏差较大的部门]
**资源优化**：[调剂建议]
**效率提升**：[流程优化机会]

## 财务建议

### 立即行动（30 天内）
**现金流**：[优化现金头寸的行动]
**降本**：[具体的降本机会，附预计节省金额]
**增收**：[增收策略和落地时间]

### 战略举措（90 天以上）
**投资方向**：[资金分配建议，附 ROI 预测]
**融资策略**：[最优资本结构和融资建议]
**风险管理**：[财务风险对冲策略]
**绩效改善**：[长期效率和盈利能力提升方案]

### 财务管控
**流程改进**：[流程优化和自动化机会]
**合规更新**：[监管变化和合规要求]
**审计准备**：[文档和管控改善]
**报表升级**：[看板和报表系统改进]

---
**财务追踪员**：[姓名]
**报告日期**：[日期]
**覆盖期间**：[期间]
**下次评审**：[计划评审日期]
**审批状态**：[管理层审批进度]
```
