## 技术交付物

### k6 压测脚本示例

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// 自定义指标
const errorRate = new Rate('errors');
const apiDuration = new Trend('api_duration');

// 测试配置：阶梯式负载
export const options = {
  stages: [
    { duration: '2m', target: 50 },   // 预热
    { duration: '5m', target: 200 },   // 正常负载
    { duration: '3m', target: 500 },   // 峰值负载
    { duration: '2m', target: 800 },   // 压力测试
    { duration: '3m', target: 0 },     // 冷却
  ],
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    errors: ['rate<0.01'],  // 错误率 < 1%
  },
};

const BASE_URL = __ENV.BASE_URL || 'https://api.example.com';

export default function () {
  // 场景 1：获取用户列表（读操作，占 60% 流量）
  const listResp = http.get(`${BASE_URL}/api/v1/users?page=1`, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
    tags: { name: 'GET /users' },
  });

  check(listResp, {
    'list status is 200': (r) => r.status === 200,
    'list has data': (r) => JSON.parse(r.body).data.length > 0,
  });

  errorRate.add(listResp.status !== 200);
  apiDuration.add(listResp.timings.duration);

  sleep(1);

  // 场景 2：创建资源（写操作，占 20% 流量）
  if (Math.random() < 0.33) {
    const createResp = http.post(
      `${BASE_URL}/api/v1/items`,
      JSON.stringify({
        name: `test-item-${Date.now()}`,
        description: '性能测试数据',
      }),
      {
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${__ENV.TOKEN}`,
        },
        tags: { name: 'POST /items' },
      }
    );

    check(createResp, {
      'create status is 201': (r) => r.status === 201,
    });

    errorRate.add(createResp.status !== 201);
  }

  sleep(Math.random() * 3);
}
```

### 性能测试报告模板

```markdown
# 性能测试报告

## 测试概要
- **版本**：v2.4.0 vs v2.3.0（对比测试）
- **环境**：4C8G x 3 节点，PostgreSQL 4C16G
- **数据量**：用户表 100 万行，订单表 500 万行
- **测试工具**：k6 v0.48

## 关键指标对比
| 指标 | v2.3.0 | v2.4.0 | 变化 |
|------|--------|--------|------|
| QPS 峰值 | 1,200 | 1,850 | +54% |
| P50 延迟 | 45ms | 28ms | -38% |
| P95 延迟 | 230ms | 95ms | -59% |
| P99 延迟 | 890ms | 320ms | -64% |
| 错误率 | 0.8% | 0.1% | -87% |
| CPU 峰值 | 92% | 68% | -26% |

## 瓶颈分析
v2.3.0 的主要瓶颈：数据库慢查询（订单列表未命中索引）
v2.4.0 的优化：添加复合索引 + 查询改写

## 容量建议
当前配置可支撑 QPS 1,500（80% 水位线）。
按月增长 10% 预估，3 个月后需要扩容到 5 节点。
```
