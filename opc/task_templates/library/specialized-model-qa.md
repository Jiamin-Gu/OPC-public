## 技术交付物

### 群体稳定性指数（PSI）

```python
import numpy as np
import pandas as pd

def compute_psi(expected: pd.Series, actual: pd.Series, bins: int = 10) -> float:
    """
    计算两个分布之间的群体稳定性指数。

    解读：
      < 0.10  → 无显著偏移（绿灯）
      0.10–0.25 → 中度偏移，建议调查（黄灯）
      >= 0.25 → 显著偏移，需采取行动（红灯）
    """
    breakpoints = np.linspace(0, 100, bins + 1)
    expected_pcts = np.percentile(expected.dropna(), breakpoints)

    expected_counts = np.histogram(expected, bins=expected_pcts)[0]
    actual_counts = np.histogram(actual, bins=expected_pcts)[0]

    # 拉普拉斯平滑避免除零
    exp_pct = (expected_counts + 1) / (expected_counts.sum() + bins)
    act_pct = (actual_counts + 1) / (actual_counts.sum() + bins)

    psi = np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct))
    return round(psi, 6)
```

### 区分度指标（Gini & KS）

```python
from sklearn.metrics import roc_auc_score
from scipy.stats import ks_2samp

def discrimination_report(y_true: pd.Series, y_score: pd.Series) -> dict:
    """
    计算二分类器的核心区分度指标。
    返回 AUC、Gini 系数和 KS 统计量。
    """
    auc = roc_auc_score(y_true, y_score)
    gini = 2 * auc - 1
    ks_stat, ks_pval = ks_2samp(
        y_score[y_true == 1], y_score[y_true == 0]
    )
    return {
        "AUC": round(auc, 4),
        "Gini": round(gini, 4),
        "KS": round(ks_stat, 4),
        "KS_pvalue": round(ks_pval, 6),
    }
```

### 校准检验（Hosmer-Lemeshow）

```python
from scipy.stats import chi2

def hosmer_lemeshow_test(
    y_true: pd.Series, y_pred: pd.Series, groups: int = 10
) -> dict:
    """
    Hosmer-Lemeshow 拟合优度检验用于校准评估。
    p 值 < 0.05 表明存在显著的校准偏差。
    """
    data = pd.DataFrame({"y": y_true, "p": y_pred})
    data["bucket"] = pd.qcut(data["p"], groups, duplicates="drop")

    agg = data.groupby("bucket", observed=True).agg(
        n=("y", "count"),
        observed=("y", "sum"),
        expected=("p", "sum"),
    )

    hl_stat = (
        ((agg["observed"] - agg["expected"]) ** 2)
        / (agg["expected"] * (1 - agg["expected"] / agg["n"]))
    ).sum()

    dof = len(agg) - 2
    p_value = 1 - chi2.cdf(hl_stat, dof)

    return {
        "HL_statistic": round(hl_stat, 4),
        "p_value": round(p_value, 6),
        "calibrated": p_value >= 0.05,
    }
```

### SHAP 特征重要性分析

```python
import shap
import matplotlib.pyplot as plt

def shap_global_analysis(model, X: pd.DataFrame, output_dir: str = "."):
    """
    通过 SHAP 值进行全局可解释性分析。
    生成汇总图（蜂群图）和平均 |SHAP| 柱状图。
    适用于树模型（XGBoost、LightGBM、RF），
    其他模型类型回退到 KernelExplainer。
    """
    try:
        explainer = shap.TreeExplainer(model)
    except Exception:
        explainer = shap.KernelExplainer(
            model.predict_proba, shap.sample(X, 100)
        )

    shap_values = explainer.shap_values(X)

    # 多输出时取正类
    if isinstance(shap_values, list):
        shap_values = shap_values[1]

    # 蜂群图：展示每个特征的值方向和幅度
    shap.summary_plot(shap_values, X, show=False)
    plt.tight_layout()
    plt.savefig(f"{output_dir}/shap_beeswarm.png", dpi=150)
    plt.close()

    # 柱状图：每个特征的平均绝对 SHAP 值
    shap.summary_plot(shap_values, X, plot_type="bar", show=False)
    plt.tight_layout()
    plt.savefig(f"{output_dir}/shap_importance.png", dpi=150)
    plt.close()

    # 返回特征重要性排名
    importance = pd.DataFrame({
        "feature": X.columns,
        "mean_abs_shap": np.abs(shap_values).mean(axis=0),
    }).sort_values("mean_abs_shap", ascending=False)

    return importance


def shap_local_explanation(model, X: pd.DataFrame, idx: int):
    """
    局部可解释性：解释单个预测。
    生成瀑布图展示每个特征如何将预测从基准值推移。
    """
    try:
        explainer = shap.TreeExplainer(model)
    except Exception:
        explainer = shap.KernelExplainer(
            model.predict_proba, shap.sample(X, 100)
        )

    explanation = explainer(X.iloc[[idx]])
    shap.plots.waterfall(explanation[0], show=False)
    plt.tight_layout()
    plt.savefig(f"shap_waterfall_obs_{idx}.png", dpi=150)
    plt.close()
```

### 偏依赖图（PDP）

```python
from sklearn.inspection import PartialDependenceDisplay

def pdp_analysis(
    model,
    X: pd.DataFrame,
    features: list[str],
    output_dir: str = ".",
    grid_resolution: int = 50,
):
    """
    关键特征的偏依赖图。
    展示每个特征对预测的边际效应，平均化所有其他特征。

    用途：
    - 验证预期的单调关系
    - 检测模型学习到的非线性阈值
    - 对比训练集与 OOT 的 PDP 形状以评估稳定性
    """
    for feature in features:
        fig, ax = plt.subplots(figsize=(8, 5))
        PartialDependenceDisplay.from_estimator(
            model, X, [feature],
            grid_resolution=grid_resolution,
            ax=ax,
        )
        ax.set_title(f"偏依赖 - {feature}")
        fig.tight_layout()
        fig.savefig(f"{output_dir}/pdp_{feature}.png", dpi=150)
        plt.close(fig)


def pdp_interaction(
    model,
    X: pd.DataFrame,
    feature_pair: tuple[str, str],
    output_dir: str = ".",
):
    """
    二维偏依赖图用于特征交互分析。
    揭示两个特征如何共同影响预测。
    """
    fig, ax = plt.subplots(figsize=(8, 6))
    PartialDependenceDisplay.from_estimator(
        model, X, [feature_pair], ax=ax
    )
    ax.set_title(f"PDP 交互 - {feature_pair[0]} x {feature_pair[1]}")
    fig.tight_layout()
    fig.savefig(
        f"{output_dir}/pdp_interact_{'_'.join(feature_pair)}.png", dpi=150
    )
    plt.close(fig)
```

### 变量稳定性监控

```python
def variable_stability_report(
    df: pd.DataFrame,
    date_col: str,
    variables: list[str],
    psi_threshold: float = 0.25,
) -> pd.DataFrame:
    """
    模型特征的月度稳定性报告。
    标记相对首个观察期 PSI 超阈值的变量。
    """
    periods = sorted(df[date_col].unique())
    baseline = df[df[date_col] == periods[0]]

    results = []
    for var in variables:
        for period in periods[1:]:
            current = df[df[date_col] == period]
            psi = compute_psi(baseline[var], current[var])
            results.append({
                "variable": var,
                "period": period,
                "psi": psi,
                "flag": "红灯" if psi >= psi_threshold else (
                    "黄灯" if psi >= 0.10 else "绿灯"
                ),
            })

    return pd.DataFrame(results).pivot_table(
        index="variable", columns="period", values="psi"
    ).round(4)
```

## 交付物模板

```markdown
# 模型 QA 报告 - [模型名称]

## 管理层摘要
**模型**：[名称和版本]
**类型**：[分类 / 回归 / 排序 / 预测 / 其他]
**算法**：[逻辑回归 / XGBoost / 神经网络 / 等]
**QA 类型**：[初始 / 定期 / 触发式]
**总体评价**：[健全 / 健全但有发现 / 不健全]

## 发现汇总
| #   | 发现       | 严重度       | 领域   | 修复措施 | 截止日期 |
| --- | --------- | ----------- | ------ | ------- | ------- |
| 1   | [描述]     | 高/中/低     | [领域] | [行动]  | [日期]   |

## 详细分析
### 1. 文档与治理 - [通过/未通过]
### 2. 数据重建 - [通过/未通过]
### 3. 目标变量/标签分析 - [通过/未通过]
### 4. 分群 - [通过/未通过]
### 5. 特征分析 - [通过/未通过]
### 6. 模型复现 - [通过/未通过]
### 7. 校准 - [通过/未通过]
### 8. 性能与监控 - [通过/未通过]
### 9. 可解释性与公平性 - [通过/未通过]
### 10. 业务影响 - [通过/未通过]

## 附录
- A：复现脚本与环境
- B：统计检验输出
- C：SHAP 汇总图与 PDP 图表
- D：特征稳定性热力图
- E：校准曲线与区分度图表

---
**QA 分析师**：[姓名]
**QA 日期**：[日期]
**下次计划审查**：[日期]
```
