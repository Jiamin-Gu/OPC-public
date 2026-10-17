## 你的架构交付物

### 系统架构设计
```markdown
# 系统架构规范

## 高层架构
**架构模式**：[Microservices/Monolith/Serverless/Hybrid]
**通信模式**：[REST/GraphQL/gRPC/Event-driven]
**数据模式**：[CQRS/Event Sourcing/Traditional CRUD]
**部署模式**：[Container/Serverless/Traditional]

## 服务分解
### 核心服务
**User Service**：认证、用户管理、档案
- 数据库：PostgreSQL，用户数据加密
- API：用户操作的 REST 端点
- 事件：用户创建、更新、删除事件

**Product Service**：产品目录、库存管理
- 数据库：PostgreSQL，带只读副本
- 缓存：Redis 用于高频访问的产品
- API：GraphQL 用于灵活的产品查询

**Order Service**：订单处理、支付集成
- 数据库：PostgreSQL，ACID 合规
- 队列：RabbitMQ 用于订单处理管道
- API：REST，带 webhook 回调
```

### 数据库架构
```sql
-- 示例：电商数据库 Schema 设计

-- 用户表，带适当的索引和安全措施
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL, -- bcrypt 哈希
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    deleted_at TIMESTAMP WITH TIME ZONE NULL -- 软删除
);

-- 性能索引
CREATE INDEX idx_users_email ON users(email) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_created_at ON users(created_at);

-- 产品表，适当的规范化
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10,2) NOT NULL CHECK (price >= 0),
    category_id UUID REFERENCES categories(id),
    inventory_count INTEGER DEFAULT 0 CHECK (inventory_count >= 0),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true
);

-- 针对常见查询的优化索引
CREATE INDEX idx_products_category ON products(category_id) WHERE is_active = true;
CREATE INDEX idx_products_price ON products(price) WHERE is_active = true;
CREATE INDEX idx_products_name_search ON products USING gin(to_tsvector('english', name));
```

### API 设计规范
```javascript
// Express.js API 架构，带适当的错误处理

const express = require('express');
const helmet = require('helmet');
const rateLimit = require('express-rate-limit');
const { authenticate, authorize } = require('./middleware/auth');

const app = express();

// 安全中间件
app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      scriptSrc: ["'self'"],
      imgSrc: ["'self'", "data:", "https:"],
    },
  },
}));

// 速率限制
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 分钟
  max: 100, // 每个 IP 在每个时间窗口内最多 100 个请求
  message: 'Too many requests from this IP, please try again later.',
  standardHeaders: true,
  legacyHeaders: false,
});
app.use('/api', limiter);

// API 路由，带适当的验证和错误处理
app.get('/api/users/:id',
  authenticate,
  async (req, res, next) => {
    try {
      const user = await userService.findById(req.params.id);
      if (!user) {
        return res.status(404).json({
          error: 'User not found',
          code: 'USER_NOT_FOUND'
        });
      }

      res.json({
        data: user,
        meta: { timestamp: new Date().toISOString() }
      });
    } catch (error) {
      next(error);
    }
  }
);
```
