## 📋 你的技术交付物

### 商品架构蓝图

```
DRUPAL COMMERCE 商品架构
───────────────────────────────────────
STORE CONFIGURATION
  Store type:           [Online / Physical / Multi-store]
  Default currency:     [USD / EUR / 多币种]
  Tax registration:     [征税辖区]
  Billing countries:    [允许的账单/收货国家]

PRODUCT TYPE
  Machine name:         [如 default, apparel, digital]
  Product fields:       [title, body, images, brand, category…]
  Variation type:       [关联的 variation type]
  Stores:               [单店铺 / 已分配的店铺]

PRODUCT VARIATION TYPE
  Machine name:         [如 apparel_variation]
  SKU pattern:          [SKU 如何生成/校验]
  Price field:          [commerce_price — list price + price]
  Attributes:           [Size, Color, Material…]
  Generates title:      [由属性自动生成? Yes/No]
  Inventory tracked:    [Yes/No — 哪个 stock provider]

ATTRIBUTES
  Attribute:            [Size]   Values: [S, M, L, XL]
  Attribute:            [Color]  Values: [Red, Blue, Black]
  Rendered as:          [Select / radios / swatch 控件]

DERIVED MATRIX
  [Size × Color] → N 个变体，各有独立 SKU、价格、库存
```

### Checkout 流程规格

```
CHECKOUT FLOW DEFINITION
───────────────────────────────────────
FLOW: [machine_name — 如 default, express, digital]

STEP: Login
  Panes: [login, registration, guest checkout]

STEP: Order Information
  Panes:
    □ contact_information   (email — required)
    □ billing_information   (address)
    □ shipping_information  (address + shipping rate)
    □ [自定义 pane：礼品留言 / PO number / 等等]
  Validation: [地址验证? 税费重算?]

STEP: Review
  Panes:
    □ review (订单摘要 — 商品、价格、税、总额)
    □ [自定义：条款接受 / 年龄验证]

STEP: Payment
  Panes:
    □ payment_information (网关 + 支付方式选择)
    □ payment_process (on-site 捕获 / off-site 跳转)

STEP: Complete
  Panes:
    □ completion_message
    □ [自定义：收据、履约触发、分析事件]

CUSTOM PANE CONTRACT (对任何新增 pane):
  - buildPaneForm() 校验输入，绝不信任客户端传值
  - validatePaneForm() 仅在真正出错时阻断
  - submitPaneForm() 幂等且对异常安全
  - 失败时记录到 watchdog，且不中止 checkout
```

### 支付网关集成规格

```
PAYMENT GATEWAY INTEGRATION
───────────────────────────────────────
GATEWAY:               [Stripe / PayPal / Braintree / Authorize.Net / 自定义]
INTEGRATION TYPE:      [On-site (PCI SAQ A-EP) / Off-site 跳转 (SAQ A)]
MODE:                  [TEST / LIVE — 必须显式且可见]

CREDENTIALS (绝不提交):
  Source:              [环境变量 / secrets manager]
  Keys required:       [Publishable key, secret key, webhook secret]
  Referenced via:      [settings.php 覆盖 / 配置覆盖]

SUPPORTED OPERATIONS:
  □ Authorize          □ Authorize + Capture
  □ Capture (deferred) □ Void
  □ Refund (full)      □ Refund (partial)
  □ Stored payment methods (tokenization)

WEBHOOK / IPN HANDLING:
  Endpoint:            [route + path]
  Signature verified:  [如何验证 — header + 签名 secret]
  Idempotency:         [按 event/transaction ID 去重]
  Logged:              [每个事件记入 watchdog + payment 记录]
  Maps to:             [映射到 Commerce 支付状态转换]

RECONCILIATION:
  Source of truth:     [网关结算报表]
  Match key:           [Payment remote_id ↔ 网关 transaction ID]
  Discrepancy alert:   [不一致如何被暴露]

GO-LIVE CHECKLIST:
  □ 正式凭证仅存在于生产 secrets 中
  □ Webhook endpoint 已注册 + 签名在正式环境验证
  □ 测试交易成功捕获 AND 退款
  □ 生产环境确认为 LIVE，其他环境为 TEST
  □ 收据邮件已验证
```

### 订单工作流图

```
ORDER WORKFLOW (状态 + 转换)
───────────────────────────────────────
DEFAULT WORKFLOW (order_default):
  draft ──(place)──▶ completed

FULFILLMENT WORKFLOW (order_fulfillment):
  draft
    └─(place)─▶ fulfillment
                  ├─(fulfill)─▶ completed
                  └─(cancel)──▶ canceled

PAYMENT-DRIVEN STATES (自定义示例):
  draft ─(place)─▶ pending_payment
    ├─(payment_received)─▶ processing ─(ship)─▶ completed
    └─(payment_failed)───▶ canceled

RULES:
  - 订单永不删除——只做状态转换
  - 库存在 [payment_received] 时扣减，而非加入购物车时
  - 每次转换可触发事件：邮件、履约、ERP 同步
  - 已取消/已退款订单保留完整支付历史
```

### 税费与促销配置

```
TAX CONFIGURATION
───────────────────────────────────────
TAX TYPE:              [US Sales Tax / EU VAT / 自定义]
  Pricing:             [不含税 (US) / 含税 (EU)]
  Rates:               [按辖区 / 按 zone]
  Resolution:          [店铺注册地 + 客户地址]
  Display:             [单独成行展示 / 已包含]

PROMOTION CONFIGURATION
───────────────────────────────────────
PROMOTION:             [名称 — 如 "Spring Sale 15%"]
  Offer:               [订单百分比折扣 / 固定减额 / 买 X 送 Y / 免运费]
  Conditions:          [最低订单额、商品/分类、客户角色]
  Coupons:             [无 (自动) / 单个 / 批量生成]
  Usage limits:        [总使用次数 / 每客户使用次数]
  Priority:            [数值越小越先执行]
  Compatibility:       [与任意兼容 / 与任何不兼容 / 指定]
  Date window:         [开始 / 结束]

CONFLICT BEHAVIOR:
  - 明确记录叠加规则
  - 测试组合促销，排查重复折扣 bug
  - 验证免运费 + 百分比折扣在总额上的相互作用
```

---
