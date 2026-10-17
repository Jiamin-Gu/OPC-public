## 📋 你的技术交付物

### 威胁模型文档
```markdown
# 威胁模型：[应用名称]

**日期**：[YYYY-MM-DD] | **版本**：[1.0] | **作者**：安全工程师

## 系统概览
- **架构**：[单体 / 微服务 / 无服务器 / 混合]
- **技术栈**：[语言、框架、数据库、云厂商]
- **数据分级**：[PII、金融、健康/PHI、凭据、公开]
- **部署**：[Kubernetes / ECS / Lambda / 基于虚拟机]
- **外部集成**：[支付处理商、OAuth 提供方、第三方 API]

## 信任边界
| 边界 | 来自 | 到达 | 控制措施 |
|------|------|------|----------|
| Internet → 应用 | 终端用户 | API 网关 | TLS、WAF、限速 |
| API → 服务 | API 网关 | 微服务 | mTLS、JWT 校验 |
| 服务 → 数据库 | 应用 | 数据库 | 参数化查询、加密连接 |
| 服务 → 服务 | 微服务 A | 微服务 B | mTLS、服务网格策略 |

## STRIDE 分析
| 威胁 | 组件 | 风险 | 攻击场景 | 缓解措施 |
|------|------|------|----------|----------|
| 仿冒（Spoofing） | 认证端点 | 高 | 撞库、令牌窃取 | MFA、令牌绑定、账户锁定 |
| 篡改（Tampering） | API 请求 | 高 | 参数篡改、请求重放 | HMAC 签名、输入校验、幂等键 |
| 抵赖（Repudiation） | 用户操作 | 中 | 否认未授权交易 | 带防篡改存储的不可变审计日志 |
| 信息泄露（Info Disclosure） | 错误响应 | 中 | 堆栈跟踪泄露内部架构 | 通用错误响应、结构化日志 |
| 拒绝服务（DoS） | 公开 API | 高 | 资源耗尽、算法复杂度攻击 | 限速、WAF、熔断器、请求大小限制 |
| 权限提升（Elevation of Privilege） | 管理面板 | 严重 | 通过 IDOR 触及管理功能、JWT 角色篡改 | 服务端强制的 RBAC、会话隔离 |

## 攻击面清单
- **外部**：公开 API、OAuth/OIDC 流程、文件上传、WebSocket 端点、GraphQL
- **内部**：服务间 RPC、消息队列、共享缓存、内部 API
- **数据**：数据库查询、缓存层、日志存储、备份系统
- **基础设施**：容器编排、CI/CD 流水线、密钥管理、DNS
- **供应链**：第三方依赖、CDN 托管脚本、外部 API 集成
```

### 安全代码评审范式
```python
# 示例：带认证、校验与限速的安全 API 端点

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address
import re

app = FastAPI(docs_url=None, redoc_url=None)  # 生产环境禁用文档
security = HTTPBearer()
limiter = Limiter(key_func=get_remote_address)

class UserInput(BaseModel):
    """严格的输入校验——拒绝任何预期之外的内容。"""
    username: str = Field(..., min_length=3, max_length=30)
    email: str = Field(..., max_length=254)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("Username contains invalid characters")
        return v

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """校验 JWT——签名、过期、签发者、受众。绝不允许 alg=none。"""
    try:
        payload = jwt.decode(
            credentials.credentials,
            key=settings.JWT_PUBLIC_KEY,
            algorithms=["RS256"],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        return payload
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

@app.post("/api/users", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_user(request: Request, user: UserInput, auth: dict = Depends(verify_token)):
    # 1. 认证由依赖注入处理——在处理函数运行前就先行失败
    # 2. 输入由 Pydantic 校验——在边界处拒绝畸形数据
    # 3. 已限速——防止滥用与撞库
    # 4. 使用参数化查询——SQL 绝不用字符串拼接
    # 5. 返回最小化数据——不带内部 ID、不带堆栈跟踪
    # 6. 把安全事件记入审计日志（而非客户端响应）
    audit_log.info("user_created", actor=auth["sub"], target=user.username)
    return {"status": "created", "username": user.username}
```

### CI/CD 安全流水线
```yaml
# GitHub Actions 安全扫描
name: Security Scan
on:
  pull_request:
    branches: [main]

jobs:
  sast:
    name: Static Analysis
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Semgrep SAST
        uses: semgrep/semgrep-action@v1
        with:
          config: >-
            p/owasp-top-ten
            p/cwe-top-25

  dependency-scan:
    name: Dependency Audit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Trivy vulnerability scanner
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          severity: 'CRITICAL,HIGH'
          exit-code: '1'

  secrets-scan:
    name: Secrets Detection
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Run Gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```
