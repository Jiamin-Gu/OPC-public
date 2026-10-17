### 企微SCRM系统配置方案

```yaml
# 企微SCRM核心配置
scrm_config:
  # 渠道活码配置
  channel_codes:
    - name: "包裹卡-华东仓"
      type: "auto_assign"
      staff_pool: ["sales_team_east"]
      welcome_message: "Hi~我是你的专属顾问{staff_name}，感谢购买！回复1领取VIP社群邀请，回复2获取产品使用指南"
      auto_tags: ["包裹卡", "华东", "新客户"]
      channel_tracking: "parcel_card_east"

    - name: "直播间引流码"
      type: "round_robin"
      staff_pool: ["live_team"]
      welcome_message: "直播间的朋友你好！发送「直播福利」领取专属优惠券~"
      auto_tags: ["直播引流", "高意向"]

    - name: "门店导流码"
      type: "location_based"
      staff_pool: ["store_staff_{city}"]
      welcome_message: "欢迎光临{store_name}！我是您的专属导购，后续有任何需要随时找我"
      auto_tags: ["门店客户", "{city}", "{store_name}"]

  # 客户标签体系
  tag_system:
    dimensions:
      - name: "客户来源"
        tags: ["包裹卡", "直播间", "门店", "短信", "老客推荐", "自然搜索"]
      - name: "消费能力"
        tags: ["高客单(>500)", "中客单(200-500)", "低客单(<200)"]
      - name: "生命周期"
        tags: ["新客户", "活跃客户", "沉默客户", "流失预警", "已流失"]
      - name: "兴趣偏好"
        tags: ["护肤", "彩妆", "个护", "母婴", "保健"]
    auto_tagging_rules:
      - trigger: "首次购买完成"
        add_tags: ["新客户"]
        remove_tags: []
      - trigger: "30天未互动"
        add_tags: ["沉默客户"]
        remove_tags: ["活跃客户"]
      - trigger: "累计消费>2000"
        add_tags: ["高价值客户", "VIP候选"]

  # 客户群配置
  group_config:
    types:
      - name: "引流福利群"
        max_members: 200
        auto_welcome: "欢迎加入！群内每天分享好物推荐和专属福利，先看置顶群公告了解群规~"
        sop_template: "welfare_group_sop"
      - name: "VIP会员群"
        max_members: 100
        entry_condition: "累计消费>1000 OR 标签含'VIP'"
        auto_welcome: "恭喜成为VIP会员！这里有专属折扣、新品优先试用和1v1顾问服务"
        sop_template: "vip_group_sop"
```

### 社群运营SOP模板

```markdown
# 福利群每日运营SOP

## 每日内容排期
| 时间 | 栏目 | 内容示例 | 触达方式 | 目的 |
|------|------|---------|---------|------|
| 08:30 | 早安问候 | 今日天气+护肤小贴士 | 群消息 | 养成打开习惯 |
| 10:00 | 好物种草 | 单品深度测评（图文） | 群消息+小程序卡片 | 内容价值输出 |
| 12:30 | 午间互动 | 投票/话题讨论/猜价格 | 群消息 | 提升活跃度 |
| 15:00 | 限时秒杀 | 小程序秒杀链接（限量30份） | 群消息+倒计时 | 转化成交 |
| 19:30 | 用户晒单 | 精选买家秀+点评 | 群消息 | 社交证明 |
| 21:00 | 晚安福利 | 明日预告+口令红包 | 群消息 | 次日留存 |

## 每周特别活动
| 周几 | 活动 | 说明 |
|------|------|------|
| 周一 | 新品尝鲜价 | VIP群专属新品折扣 |
| 周三 | 直播预告+专属券 | 引导观看视频号直播 |
| 周五 | 周末囤货日 | 满减/组合优惠 |
| 周日 | 一周热销榜 | 数据回顾+下周预告 |

## 关键节点SOP
### 新人入群（前72小时）
1. 0min：自动发送欢迎语+群规
2. 30min：管理员@新成员，引导自我介绍
3. 2h：私聊发送新人专属券（满99减20）
4. 24h：推送群内精华内容合集
5. 72h：邀请参与当日活动，完成首次互动
```

### 用户生命周期自动化流程

```python
# 用户生命周期自动化触达配置
lifecycle_automation = {
    "新客激活": {
        "trigger": "添加企微好友",
        "flows": [
            {"delay": "0min", "action": "发送欢迎语+新人礼包"},
            {"delay": "30min", "action": "推送产品使用指南(小程序)"},
            {"delay": "24h", "action": "邀请加入福利群"},
            {"delay": "48h", "action": "发送首单专属优惠券(满99减30)"},
            {"delay": "72h", "condition": "未下单", "action": "1v1私聊需求诊断"},
            {"delay": "7d", "condition": "仍未下单", "action": "发送限时体验装申领"},
        ]
    },
    "复购提醒": {
        "trigger": "上次购买后N天（根据品类消耗周期）",
        "flows": [
            {"delay": "消耗周期-7d", "action": "推送使用效果调研"},
            {"delay": "消耗周期-3d", "action": "发送复购优惠(老客专属价)"},
            {"delay": "消耗周期", "action": "1v1提醒补货+推荐升级款"},
        ]
    },
    "沉默唤醒": {
        "trigger": "30天无互动+无消费",
        "flows": [
            {"delay": "30d", "action": "朋友圈精准触达(仅沉默客户可见)"},
            {"delay": "45d", "action": "发送专属回归礼券(无门槛20元)"},
            {"delay": "60d", "action": "1v1关怀消息(非营销,纯关心)"},
            {"delay": "90d", "condition": "仍无响应", "action": "降级为低优先级,减少触达频率"},
        ]
    },
    "流失预警": {
        "trigger": "流失概率模型评分>0.7",
        "features": [
            "最近30天打开消息次数",
            "最近消费距今天数",
            "社群发言频率变化",
            "朋友圈互动下降幅度",
            "退群/屏蔽行为",
        ],
        "action": "触发人工介入,由高级顾问1v1跟进"
    }
}
```

### 转化漏斗数据看板

```sql
-- 私域转化漏斗核心指标SQL（对接BI看板）
-- 数据源：企微SCRM + 小程序订单 + 用户行为日志

-- 1. 渠道引流效率
SELECT
    channel_code_name AS 渠道,
    COUNT(DISTINCT user_id) AS 新增好友数,
    SUM(CASE WHEN first_reply_time IS NOT NULL THEN 1 ELSE 0 END) AS 首次互动数,
    ROUND(SUM(CASE WHEN first_reply_time IS NOT NULL THEN 1 ELSE 0 END)
        * 100.0 / COUNT(DISTINCT user_id), 1) AS 互动转化率
FROM scrm_user_channel
WHERE add_date BETWEEN '{start_date}' AND '{end_date}'
GROUP BY channel_code_name
ORDER BY 新增好友数 DESC;

-- 2. 社群转化漏斗
SELECT
    group_type AS 群类型,
    COUNT(DISTINCT member_id) AS 群成员数,
    COUNT(DISTINCT CASE WHEN has_clicked_product = 1 THEN member_id END) AS 点击商品数,
    COUNT(DISTINCT CASE WHEN has_ordered = 1 THEN member_id END) AS 下单人数,
    ROUND(COUNT(DISTINCT CASE WHEN has_ordered = 1 THEN member_id END)
        * 100.0 / COUNT(DISTINCT member_id), 2) AS 群转化率
FROM scrm_group_conversion
WHERE stat_date BETWEEN '{start_date}' AND '{end_date}'
GROUP BY group_type;

-- 3. 用户LTV分层
SELECT
    lifecycle_stage AS 生命周期阶段,
    COUNT(DISTINCT user_id) AS 用户数,
    ROUND(AVG(total_gmv), 2) AS 平均累计消费,
    ROUND(AVG(order_count), 1) AS 平均订单数,
    ROUND(AVG(total_gmv) / AVG(DATEDIFF(CURDATE(), first_add_date)), 2) AS 日均贡献
FROM scrm_user_ltv
GROUP BY lifecycle_stage
ORDER BY 平均累计消费 DESC;
```
