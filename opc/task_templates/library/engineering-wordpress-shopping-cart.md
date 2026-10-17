## 📋 你的技术交付物

### 商品架构蓝图

```
WOOCOMMERCE 商品架构
───────────────────────────────────────
店铺配置
  销售地区：       [指定国家 / 全部 / 全部除…之外]
  币种：           [USD / EUR / 多币种 plugin]
  价格录入方式：   [含税 / 不含税]
  税费计算依据：   [顾客 shipping / billing / 店铺地址]

商品类型
  类型：           [Simple / Variable / Grouped / External / Subscription]
  目录字段：       [名称、描述、图片、分类、标签、品牌]
  库存：           [是否管理库存？Y/N — 库存数量、缺货下单]
  配送：           [重量、尺寸、shipping class]

变体商品设置
  属性：           [是否用于变体？Y/N]
    属性：         [Size]   值：[S, M, L, XL]
    属性：         [Color]  值：[Red, Blue, Black]
  变体：           [按属性组合生成]
  每变体：         [SKU、价格、促销价、库存、图片]

定价
  原价：           [基准价]
  促销价：         [可选 + 排期]
  Tax class：      [Standard / Reduced / Zero / 自定义]
```

### Checkout 定制规格

```
CHECKOUT 配置
───────────────────────────────────────
CHECKOUT 类型：    [Block checkout（推荐）/ 经典 shortcode]

字段：
  标准：           [Billing、shipping、contact — 哪些必填]
  自定义字段：     [礼品留言 / 公司 / VAT ID / 配送日期]
  添加方式：       [Block checkout：Store API + extension
                     经典：woocommerce_checkout_fields filter]

定制契约：
  - Block checkout 定制使用 Store API / Checkout Blocks
    的扩展能力——而不是会在更新时失效的 jQuery DOM 改动
  - 经典 checkout 使用有文档记录的 hook/filter
  - 自定义字段数据保存到 order meta + 在后台和邮件中展示
  - 验证放在服务端（绝不信任客户端）；优雅地失败
  - 失败的自定义字段绝不能悄无声息地阻断订单完成

流程校验（每次部署都在移动端测试）：
  □ 加入购物车         □ 修改数量
  □ 应用优惠券         □ 计算配送费
  □ 计算税费           □ 输入支付信息
  □ 下单               □ 收到订单邮件
  □ 订单在后台出现，且合计金额 + 自定义字段正确
```

### 支付 Gateway 集成规格

```
PAYMENT GATEWAY 集成
───────────────────────────────────────
GATEWAY：          [WooPayments / Stripe / PayPal / Square / Authorize.Net]
集成类型：         [Hosted fields/redirect (SAQ A) / direct (SAQ A-EP)]
模式：             [SANDBOX/TEST / LIVE — 在后台明确且可见]

凭据（绝不明文入库 / 不进提交的代码）：
  来源：           [wp-config.php 常量 / 环境变量]
  所需密钥：       [Publishable key、secret key、webhook secret]

支持的操作：
  □ Authorize          □ Authorize + Capture
  □ Capture（延迟捕获）□ Void
  □ Refund（全额）     □ Refund（部分）
  □ 保存的卡（tokenization / SCA-3DS）

WEBHOOK / IPN 处理：
  端点：           [WC API endpoint / REST route]
  签名已验证：     [Header + 签名 secret]
  幂等性：         [按 event/transaction ID 去重]
  已记录日志：     [通过 WC_Logger 记录每个事件]
  映射到：         [订单状态流转]

对账：
  事实来源：       [Gateway 的结算/打款报表]
  匹配键：         [订单 transaction ID ↔ gateway charge ID]
  差异告警：       [不一致如何暴露出来]

上线清单：
  □ Live 密钥只在生产 wp-config 中
  □ Webhook 已注册 + live 下签名已验证
  □ 测试 charge 成功捕获并成功退款
  □ 生产确认为 LIVE，其他环境为 SANDBOX
  □ 订单 + 后台邮件已验证
```

### 订单流程图

```
WOOCOMMERCE 订单状态 + 流转
───────────────────────────────────────
标准生命周期：
  pending ──(收到支付)──▶ processing ──(已履约)──▶ completed
     │
     ├──(支付失败)──▶ failed
     └──(未付款超时)──▶ cancelled

其他状态：
  on-hold     [等待支付确认 / 人工审核]
  refunded    [已全额或部分退款 — 订单保留]
  cancelled   [未履约、未扣款 — 记录保留]

自定义状态（示例）：
  processing ─▶ wc-packed ─▶ wc-shipped ─▶ completed
  （通过 register_post_status + woocommerce_order_statuses 注册）

规则：
  - 订单永不删除——只做流转/退款
  - 库存在 [processing] 时扣减（或按设置），取消/退款时恢复
  - 每次流转都触发 hook：邮件、履约、ERP/3PL 同步、分析
  - 退款保留完整的支付 + 行项目历史
```

### 税费与优惠券配置

```
税费配置
───────────────────────────────────────
税费状态：         [是否启用税费？Y/N]
  价格录入方式：   [含税 / 不含税]
  计算依据：       [顾客 shipping / billing / 店铺基准]
  Tax class：      [Standard / Reduced rate / Zero rate / 自定义]
  税率：           [按国家/州/邮编 — 标准税率表]
  展示：           [在店铺 + 购物车中显示含税/不含税价]

优惠券配置
───────────────────────────────────────
优惠券：           [代码 — 例如 SPRING15]
  折扣类型：       [百分比折扣 / 固定金额(整单) / 固定金额(单品)]
  额度：           [数值]
  限制：           [最低/最高消费、商品/分类、排除促销品]
  使用上限：       [每优惠券 / 每用户 / X 件]
  仅可单独使用：   [Y/N — 阻止与其他优惠券叠加]
  有效期：         [日期]

叠加行为：
  - 记录优惠券是可组合还是仅可单独使用
  - 测试优惠券 + 促销价 + 税费组合对合计的影响
  - 验证免运费优惠券 + 百分比折扣的算法
```

---
