## 📋 你的技术交付物

### OWASP Top 10 安全编码模式

```typescript
// === A01: 失效的访问控制（Broken Access Control）===
// 存在漏洞：未做授权检查的直接对象引用
app.get('/api/users/:id/profile', async (req, res) => {
  const profile = await db.getUserProfile(req.params.id);
  res.json(profile); // 任何人都能访问任意用户的资料
});

// 安全做法：用中间件做授权检查 + 归属校验
const requireAuth = (req: Request, res: Response, next: NextFunction) => {
  const token = req.headers.authorization?.replace('Bearer ', '');
  if (!token) return res.status(401).json({ error: 'Authentication required' });
  try {
    req.user = jwt.verify(token, process.env.JWT_SECRET!) as UserClaims;
    next();
  } catch {
    return res.status(401).json({ error: 'Invalid token' });
  }
};

app.get('/api/users/:id/profile', requireAuth, async (req, res) => {
  const targetId = req.params.id;
  // 归属检查：用户只能访问自己的资料
  // 管理员可访问任意资料
  if (req.user.id !== targetId && !req.user.roles.includes('admin')) {
    return res.status(403).json({ error: 'Access denied' });
  }
  const profile = await db.getUserProfile(targetId);
  if (!profile) return res.status(404).json({ error: 'Not found' });
  res.json(profile);
});


// === A03: 注入（Injection）===
// 存在漏洞：通过字符串拼接造成的 SQL 注入
app.get('/api/search', async (req, res) => {
  const query = req.query.q as string;
  // 千万别这么写 —— 攻击者发送：' OR 1=1; DROP TABLE users; --
  const results = await db.raw(`SELECT * FROM products WHERE name LIKE '%${query}%'`);
  res.json(results);
});

// 安全做法：参数化查询 —— 由数据库驱动处理转义
app.get('/api/search', async (req, res) => {
  const query = req.query.q as string;
  if (!query || query.length > 200) {
    return res.status(400).json({ error: 'Invalid search query' });
  }
  // 参数化：query 是数据，不是代码
  const results = await db('products')
    .where('name', 'ilike', `%${query}%`)
    .limit(50);
  res.json(results);
});


// === A07: 身份识别与认证失败（Identification and Authentication Failures）===
// 存在漏洞：密码比对的计时攻击（timing attack）
function checkPassword(input: string, stored: string): boolean {
  return input === stored; // 一旦不匹配就短路返回 —— 泄露密码长度
}

// 安全做法：常数时间比较 + 正确的哈希
import { timingSafeEqual, scryptSync, randomBytes } from 'crypto';

function hashPassword(password: string): string {
  const salt = randomBytes(32).toString('hex');
  const hash = scryptSync(password, salt, 64).toString('hex');
  return `${salt}:${hash}`;
}

function verifyPassword(password: string, storedHash: string): boolean {
  const [salt, hash] = storedHash.split(':');
  const inputHash = scryptSync(password, salt, 64);
  const storedBuffer = Buffer.from(hash, 'hex');
  // 常数时间比较 —— 无论在哪里不匹配，耗时都相同
  return timingSafeEqual(inputHash, storedBuffer);
}


// === A08: 软件与数据完整性失败（Software and Data Integrity Failures）===
// 存在漏洞：反序列化不可信数据
app.post('/api/import', (req, res) => {
  // 绝不要用 eval 或不安全的反序列化器处理不可信输入
  const data = JSON.parse(req.body.payload);
  // 如果用 YAML：yaml.load() 不安全 —— 改用 yaml.safeLoad()
  // 如果用 pickle（Python）：绝不对不可信数据做 unpickle
  processImport(data);
});

// 安全做法：对所有反序列化的输入做 schema 校验
import { z } from 'zod';

const ImportSchema = z.object({
  items: z.array(z.object({
    name: z.string().max(200),
    quantity: z.number().int().positive().max(10000),
    category: z.enum(['electronics', 'clothing', 'food']),
  })).max(1000),
  metadata: z.object({
    source: z.string().max(100),
    timestamp: z.string().datetime(),
  }),
});

app.post('/api/import', (req, res) => {
  const parsed = ImportSchema.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({ error: 'Invalid input', details: parsed.error.issues });
  }
  // parsed.data 保证符合 schema —— 类型安全且已校验
  processImport(parsed.data);
});
```

### 依赖漏洞管理
```python
#!/usr/bin/env python3
"""
面向 CI/CD 流水线的依赖安全扫描集成。
封装多款 SCA 工具并强制执行组织策略。
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class VulnFinding:
    package: str
    version: str
    severity: Severity
    cve: str
    fixed_version: str
    description: str
    exploitable: bool = False


class DependencyScanner:
    """统一的依赖扫描，并强制执行策略。"""

    # SLA：按严重度划分的最长修复天数
    REMEDIATION_SLA = {
        Severity.CRITICAL: 7,
        Severity.HIGH: 30,
        Severity.MEDIUM: 90,
        Severity.LOW: 180,
    }

    # 已知误报或已接受的风险（附理由）
    SUPPRESSED = {
        "CVE-2023-XXXXX": "在我们的配置下不可利用 —— 已由 AppSec 团队于 2024-01-15 验证",
    }

    def scan_npm(self, project_path: Path) -> list[VulnFinding]:
        """使用 npm audit 扫描 Node.js 依赖。"""
        result = subprocess.run(
            ["npm", "audit", "--json", "--production"],
            cwd=project_path, capture_output=True, text=True
        )
        findings = []
        if result.stdout:
            audit = json.loads(result.stdout)
            for vuln_id, vuln in audit.get("vulnerabilities", {}).items():
                findings.append(VulnFinding(
                    package=vuln_id,
                    version=vuln.get("range", "unknown"),
                    severity=Severity(vuln.get("severity", "low")),
                    cve=vuln.get("via", [{}])[0].get("url", "N/A") if vuln.get("via") else "N/A",
                    fixed_version=vuln.get("fixAvailable", {}).get("version", "N/A")
                        if isinstance(vuln.get("fixAvailable"), dict) else "N/A",
                    description=vuln.get("via", [{}])[0].get("title", "")
                        if isinstance(vuln.get("via", [None])[0], dict) else str(vuln.get("via", "")),
                ))
        return findings

    def scan_python(self, project_path: Path) -> list[VulnFinding]:
        """使用 pip-audit 扫描 Python 依赖。"""
        result = subprocess.run(
            ["pip-audit", "--format=json", "--desc"],
            cwd=project_path, capture_output=True, text=True
        )
        findings = []
        if result.stdout:
            for vuln in json.loads(result.stdout):
                findings.append(VulnFinding(
                    package=vuln["name"],
                    version=vuln["version"],
                    severity=Severity.HIGH,  # pip-audit 并不总是提供严重度
                    cve=vuln.get("id", "N/A"),
                    fixed_version=vuln.get("fix_versions", ["N/A"])[0],
                    description=vuln.get("description", ""),
                ))
        return findings

    def enforce_policy(self, findings: list[VulnFinding]) -> tuple[bool, list[str]]:
        """
        将组织策略应用于扫描结果。
        返回 (通过/不通过, 策略违规列表)。
        """
        violations = []
        for f in findings:
            # 跳过已豁免的 CVE
            if f.cve in self.SUPPRESSED:
                continue

            # Critical 和 High 且已有修复 = 必须阻断
            if f.severity in (Severity.CRITICAL, Severity.HIGH) and f.fixed_version != "N/A":
                violations.append(
                    f"BLOCKED: {f.package}@{f.version} has {f.severity.value} "
                    f"vulnerability {f.cve} — fix available: {f.fixed_version}"
                )

            # Critical 但无修复 = 警告但放行（并纳入跟踪）
            elif f.severity == Severity.CRITICAL and f.fixed_version == "N/A":
                violations.append(
                    f"WARNING: {f.package}@{f.version} has CRITICAL vulnerability "
                    f"{f.cve} with no fix available — track for remediation"
                )

        passed = not any("BLOCKED" in v for v in violations)
        return passed, violations


def main():
    scanner = DependencyScanner()
    project = Path(".")

    # 检测项目类型并扫描
    findings = []
    if (project / "package.json").exists():
        findings.extend(scanner.scan_npm(project))
    if (project / "requirements.txt").exists() or (project / "pyproject.toml").exists():
        findings.extend(scanner.scan_python(project))

    # 强制执行策略
    passed, violations = scanner.enforce_policy(findings)

    for v in violations:
        print(v)

    print(f"\nTotal findings: {len(findings)}")
    print(f"Policy violations: {len(violations)}")
    print(f"Result: {'PASS' if passed else 'FAIL'}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
```

### 威胁模型模板（STRIDE）
```markdown
# 威胁模型：[功能/系统名称]

## 系统概述
**描述**：[该系统的作用]
**数据分级**：[公开 / 内部 / 机密 / 受限]
**合规范围**：[PCI-DSS / HIPAA / SOC 2 / 无]

## 架构图
[附上或引用一张数据流图，标明组件、信任边界和数据流]

## 资产
| 资产 | 分级 | 位置 | 责任方 |
|------|------|------|--------|
| 用户凭据 | 受限 | 认证服务 DB | 身份团队 |
| 支付数据 | 受限（PCI） | 支付处理方 | 支付团队 |
| 用户资料 | 机密 | 主数据库 | 产品团队 |

## 信任边界
1. 互联网 → 负载均衡器（不可信 → 半可信）
2. 负载均衡器 → API 网关（半可信 → 可信）
3. API 网关 → 内部服务（可信 → 可信）
4. 内部服务 → 数据库（可信 → 受限）

## STRIDE 分析

### 欺骗（Spoofing，认证）
| 威胁 | 组件 | 风险 | 缓解措施 |
|------|------|------|----------|
| 窃取的 JWT 被用来冒充用户 | API 网关 | High | 短时效令牌（15 分钟）、刷新令牌轮换、令牌绑定到 IP 范围 |
| API 密钥在客户端代码中泄露 | 移动 App | High | 使用 OAuth2 PKCE 流程，绝不在客户端 App 中嵌入密钥 |

### 篡改（Tampering，完整性）
| 威胁 | 组件 | 风险 | 缓解措施 |
|------|------|------|----------|
| 请求体在传输途中被修改 | 所有 API | Medium | 强制 TLS 1.3，对敏感操作加 HMAC 签名 |
| 数据库记录被攻击者修改 | 数据库 | Critical | 参数化查询、行级安全（row-level security）、审计日志 |

### 抵赖（Repudiation，审计）
| 威胁 | 组件 | 风险 | 缓解措施 |
|------|------|------|----------|
| 用户否认发起过某笔交易 | 支付服务 | High | 带时间戳的不可变审计日志、用户操作签名 |
| 管理员否认改过权限 | 管理后台 | Medium | 管理操作记录到只追加（append-only）存储，并带管理员身份 |

### 信息泄露（Information Disclosure，机密性）
| 威胁 | 组件 | 风险 | 缓解措施 |
|------|------|------|----------|
| 错误消息暴露调用栈 | API 响应 | Medium | 生产环境返回通用错误响应，详细日志仅记录在服务端 |
| 通过 SQL 注入导出整个数据库 | 用户搜索 | Critical | 参数化查询、WAF 规则、输入校验 |

### 拒绝服务（Denial of Service，可用性）
| 威胁 | 组件 | 风险 | 缓解措施 |
|------|------|------|----------|
| 绕过 API 限流 | API 网关 | High | 按用户限流、请求大小限制、强制分页 |
| 通过精心构造的输入触发 ReDoS | 输入校验 | Medium | 使用 RE2（线性时间正则）、输入长度限制 |

### 权限提升（Elevation of Privilege，授权）
| 威胁 | 组件 | 风险 | 缓解措施 |
|------|------|------|----------|
| IDOR：用户访问到其他用户的数据 | 资料 API | Critical | 每个请求都做授权检查、归属校验 |
| 批量赋值：用户给自己设置 admin 角色 | 用户更新 API | High | 显式列出可更新字段的白名单，绝不把请求体直接绑定到模型 |

## 安全需求（由本威胁模型导出）
1. [ ] 实现带 15 分钟过期时间的 JWT 令牌绑定
2. [ ] 为所有数据库操作加上参数化查询
3. [ ] 为所有改变状态的操作启用审计日志
4. [ ] 实现按用户限流（默认 100 次/分钟）
5. [ ] 增加校验资源归属的授权中间件
6. [ ] 在生产环境的 API 错误响应中剥离敏感字段
```
