## 📋 你的技术交付物

### AWS 多账户安全架构（Terraform）
```hcl
# 采用以安全为核心的 OU 结构的 AWS Organization
# 落地 SCP、集中化日志与 GuardDuty

resource "aws_organizations_organization" "org" {
  feature_set = "ALL"
  enabled_policy_types = [
    "SERVICE_CONTROL_POLICY",
    "TAG_POLICY",
  ]
}

# === 服务控制策略（护栏） ===

resource "aws_organizations_policy" "deny_root_usage" {
  name        = "deny-root-account-usage"
  description = "Prevent root user actions in member accounts"
  type        = "SERVICE_CONTROL_POLICY"
  content     = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyRootActions"
        Effect    = "Deny"
        Action    = "*"
        Resource  = "*"
        Condition = {
          StringLike = {
            "aws:PrincipalArn" = "arn:aws:iam::*:root"
          }
        }
      }
    ]
  })
}

resource "aws_organizations_policy" "deny_leave_org" {
  name    = "deny-leave-organization"
  type    = "SERVICE_CONTROL_POLICY"
  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyLeaveOrg"
        Effect   = "Deny"
        Action   = ["organizations:LeaveOrganization"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_organizations_policy" "require_encryption" {
  name    = "require-s3-encryption"
  type    = "SERVICE_CONTROL_POLICY"
  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyUnencryptedS3Uploads"
        Effect    = "Deny"
        Action    = ["s3:PutObject"]
        Resource  = "*"
        Condition = {
          StringNotEquals = {
            "s3:x-amz-server-side-encryption" = "aws:kms"
          }
        }
      }
    ]
  })
}

# === 集中化安全日志 ===

resource "aws_s3_bucket" "security_logs" {
  bucket = "org-security-logs-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "security_logs" {
  bucket = aws_s3_bucket.security_logs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "security_logs" {
  bucket = aws_s3_bucket.security_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.security_logs.arn
    }
    bucket_key_enabled = true
  }
}

# Object Lock：阻止删除审计日志（合规模式）
resource "aws_s3_bucket_object_lock_configuration" "security_logs" {
  bucket = aws_s3_bucket.security_logs.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = 365
    }
  }
}

resource "aws_s3_bucket_policy" "security_logs" {
  bucket = aws_s3_bucket.security_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.security_logs.arn}/cloudtrail/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Sid       = "DenyUnsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [
          aws_s3_bucket.security_logs.arn,
          "${aws_s3_bucket.security_logs.arn}/*"
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      }
    ]
  })
}

# === GuardDuty（威胁检测） ===

resource "aws_guardduty_detector" "main" {
  enable = true
  datasources {
    s3_logs      { enable = true }
    kubernetes   { audit_logs { enable = true } }
    malware_protection { scan_ec2_instance_with_findings { ebs_volumes { enable = true } } }
  }
}

resource "aws_guardduty_organization_admin_account" "security" {
  admin_account_id = var.security_account_id
}

# === VPC Flow Logs ===

resource "aws_flow_log" "vpc" {
  vpc_id               = var.vpc_id
  traffic_type         = "ALL"
  log_destination      = aws_s3_bucket.security_logs.arn
  log_destination_type = "s3"
  max_aggregation_interval = 60

  destination_options {
    file_format        = "parquet"
    per_hour_partition = true
  }
}
```

### Kubernetes 网络策略（零信任 Pod 间通信）
```yaml
# 默认拒绝所有流量——仅显式允许
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: production
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress

---
# 仅允许 frontend → backend API 在 8080 端口通信
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-to-api
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: backend-api
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: frontend
      ports:
        - protocol: TCP
          port: 8080

---
# 允许 backend API → database 在 5432 端口通信
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-api-to-database
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: postgres
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: backend-api
      ports:
        - protocol: TCP
          port: 5432

---
# 允许所有 Pod 的 DNS 出站（服务发现所必需）
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns-egress
  namespace: production
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
```

### CI/CD 流水线安全（GitHub Actions 配合 OIDC）
```yaml
# 安全部署流水线——无长期凭据
name: Deploy to AWS
on:
  push:
    branches: [main]

permissions:
  id-token: write   # OIDC 联合所必需
  contents: read

jobs:
  security-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # 扫描 IaC 错误配置
      - name: Checkov — Infrastructure Policy Check
        uses: bridgecrewio/checkov-action@v12
        with:
          directory: ./terraform
          framework: terraform
          soft_fail: false  # 违反策略时让流水线失败
          output_format: sarif

      # 扫描泄漏的密钥
      - name: Gitleaks — Secret Detection
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      # 扫描容器镜像
      - name: Trivy — Container Vulnerability Scan
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: ${{ env.IMAGE_TAG }}
          format: sarif
          severity: CRITICAL,HIGH
          exit-code: 1  # 出现严重/高危漏洞时失败

  deploy:
    needs: security-scan
    runs-on: ubuntu-latest
    environment: production  # 需要人工审批
    steps:
      - uses: actions/checkout@v4

      # OIDC 联合——不把 AWS 访问密钥存为 secret
      - name: Configure AWS Credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/github-deploy
          aws-region: us-east-1
          role-session-name: github-${{ github.run_id }}

      - name: Terraform Apply
        run: |
          cd terraform
          terraform init -backend-config=prod.hcl
          terraform plan -out=tfplan
          terraform apply tfplan
```

### 云安全态势检查清单
```markdown
# 云安全态势评审

## 身份与访问管理
- [ ] 日常运营不使用 root/owner 账户
- [ ] 所有人类用户强制启用 MFA（管理员用硬件密钥）
- [ ] 服务账户使用工作负载身份 / IRSA / 托管身份（无长期密钥）
- [ ] IAM 策略遵循最小权限——生产环境无通配符（*）
- [ ] 休眠账户（非活跃 90 天以上）被自动禁用
- [ ] 跨账户访问使用带 external ID 的角色假定（role assumption），而非共享凭据
- [ ] 应急访问的"破玻璃"（break-glass）流程已记录并测试

## 网络安全
- [ ] 所有区域均已删除默认 VPC
- [ ] 无安全组规则允许 0.0.0.0/0 访问管理端口（22、3389）
- [ ] 所有工作负载使用私有子网——公有子网仅供负载均衡器使用
- [ ] 所有 VPC 均启用 VPC Flow Logs
- [ ] 启用 DNS 日志（Route 53 query logs / Cloud DNS logging）
- [ ] 环境之间（dev/staging/prod）做网络分段
- [ ] 访问云服务（S3、KMS、ECR）使用私有端点

## 数据保护
- [ ] 所有存储服务（S3、EBS、RDS、DynamoDB）均启用静态加密
- [ ] 敏感数据使用客户托管 KMS 密钥
- [ ] 启用密钥轮换（自动或策略强制）
- [ ] S3 存储桶在账户级别阻止公共访问
- [ ] 数据库备份已加密并记录访问日志
- [ ] 存储资源应用数据分类标签

## 日志与检测
- [ ] 所有区域/项目均启用 CloudTrail / Activity Log / Audit Log
- [ ] 日志发往集中化、不可篡改的存储
- [ ] 启用 GuardDuty / Defender for Cloud / Security Command Center
- [ ] 已为以下事件配置告警：root 登录、IAM 变更、安全组变更、从新位置登录控制台
- [ ] 日志留存满足合规要求（通常 1-7 年）

## 计算安全
- [ ] 容器镜像在部署前扫描（Trivy、Snyk、ECR 扫描）
- [ ] 容器以非 root 运行并采用只读文件系统
- [ ] EC2 实例使用 IMDSv2（hop limit = 1）——阻断 SSRF 凭据窃取
- [ ] 使用 SSM Session Manager 或同类方案替代 SSH/RDP
- [ ] 为操作系统与运行时漏洞启用自动打补丁
```
