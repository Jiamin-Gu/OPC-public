## 技术交付物

### 追踪架构方案

```markdown
# 追踪架构实施方案

## 架构总览
```
用户浏览器
  ├─ GTM Web 容器
  │   ├─ GA4 配置代码
  │   ├─ Google Ads 转化代码
  │   ├─ Meta Pixel 代码
  │   └─ Consent Mode 控制
  │
  └─ 服务端
      ├─ GTM Server 容器
      │   ├─ GA4 服务端
      │   ├─ Meta CAPI
      │   └─ 数据丰富逻辑
      └─ 第一方 Cookie 域
```

## 转化操作清单
| 转化名称 | 平台 | 类型 | 归因模型 | 窗口 |
|---------|------|------|---------|------|
| 购买 | Google Ads | 主要 | 数据驱动 | 30 天点击 |
| 加购 | Google Ads | 次要 | 数据驱动 | 7 天点击 |
| 线索提交 | Google Ads | 主要 | 数据驱动 | 30 天点击 |
| 购买 | Meta | 标准事件 | 7 天点击 / 1 天浏览 | - |
| 线索提交 | Meta | 标准事件 | 7 天点击 / 1 天浏览 | - |

## 去重策略
### Meta Pixel + CAPI
- 浏览器端和服务端同时发送事件
- 通过 event_id 字段去重
- event_id 生成逻辑：`{event_name}_{transaction_id}_{timestamp}`
- Meta 自动对匹配的 event_id 去重

### 跨平台去重
- 统一使用 transaction_id 作为去重键
- GA4 作为基准数据源
- 月度校验各平台转化数偏差

## QA 检查清单
- [ ] 所有页面 GTM 代码加载正常
- [ ] 关键事件在 GA4 DebugView 中验证通过
- [ ] Google Ads 转化计数与 GA4 偏差 < 3%
- [ ] Meta Pixel 与 CAPI 事件去重生效
- [ ] Consent Mode 在用户拒绝时正确阻止代码触发
- [ ] 服务端容器延迟 < 200ms
- [ ] 跨域追踪在所有域名间正常传递
```
