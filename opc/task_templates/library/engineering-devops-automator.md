## 技术交付物

### CI/CD 流水线架构
```yaml
# GitHub Actions 流水线示例
name: Production Deployment

on:
  push:
    branches: [main]

jobs:
  security-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Security Scan
        run: |
          # 依赖漏洞扫描
          npm audit --audit-level high
          # 静态安全分析
          docker run --rm -v $(pwd):/src securecodewarrior/docker-security-scan

  test:
    needs: security-scan
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run Tests
        run: |
          npm test
          npm run test:integration

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - name: Build and Push
        run: |
          docker build -t app:${{ github.sha }} .
          docker push registry/app:${{ github.sha }}

  deploy:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - name: Blue-Green Deploy
        run: |
          # 部署到 green 环境
          kubectl set image deployment/app app=registry/app:${{ github.sha }}
          # 健康检查
          kubectl rollout status deployment/app
          # 切换流量
          kubectl patch svc app -p '{"spec":{"selector":{"version":"green"}}}'
```

### 基础设施即代码模板
```hcl
# Terraform 基础设施示例
provider "aws" {
  region = var.aws_region
}

# 自动伸缩 Web 应用基础设施
resource "aws_launch_template" "app" {
  name_prefix   = "app-"
  image_id      = var.ami_id
  instance_type = var.instance_type

  vpc_security_group_ids = [aws_security_group.app.id]

  user_data = base64encode(templatefile("${path.module}/user_data.sh", {
    app_version = var.app_version
  }))

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "app" {
  desired_capacity    = var.desired_capacity
  max_size           = var.max_size
  min_size           = var.min_size
  vpc_zone_identifier = var.subnet_ids

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  health_check_type         = "ELB"
  health_check_grace_period = 300

  tag {
    key                 = "Name"
    value               = "app-instance"
    propagate_at_launch = true
  }
}

# Application Load Balancer
resource "aws_lb" "app" {
  name               = "app-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets           = var.public_subnet_ids

  enable_deletion_protection = false
}

# 监控与告警
resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "app-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ApplicationELB"
  period              = "120"
  statistic           = "Average"
  threshold           = "80"

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

### 监控与告警配置
```yaml
# Prometheus 配置
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

rule_files:
  - "alert_rules.yml"

scrape_configs:
  - job_name: 'application'
    static_configs:
      - targets: ['app:8080']
    metrics_path: /metrics
    scrape_interval: 5s

  - job_name: 'infrastructure'
    static_configs:
      - targets: ['node-exporter:9100']

---
# 告警规则
groups:
  - name: application.rules
    rules:
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "检测到高错误率"
          description: "错误率为每秒 {{ $value }} 个错误"

      - alert: HighResponseTime
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 0.5
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "检测到高响应时间"
          description: "95th 百分位响应时间为 {{ $value }} 秒"
```

## 交付物模板

```markdown
# [项目名称] DevOps 基础设施与自动化

## 基础设施架构

### 云平台策略
**平台**：[AWS/GCP/Azure 选型及理由]
**区域**：[多区域部署以保障高可用]
**成本策略**：[资源优化与预算管理]

### 容器与编排
**容器策略**：[Docker 容器化方案]
**编排方案**：[Kubernetes/ECS 及其配置]
**Service Mesh**：[按需实施 Istio/Linkerd]

## CI/CD 流水线

### 流水线阶段
**源码管理**：[分支保护与合并策略]
**安全扫描**：[依赖分析和静态分析工具]
**测试**：[单元测试、集成测试和端到端测试]
**构建**：[容器构建和制品管理]
**部署**：[零停机部署策略]

### 部署策略
**方式**：[蓝绿部署/金丝雀发布/滚动更新]
**回滚**：[自动回滚触发条件和流程]
**健康检查**：[应用和基础设施监控]

## 监控与可观测性

### 指标采集
**应用指标**：[自定义业务和性能指标]
**基础设施指标**：[资源利用率和健康状态]
**日志聚合**：[结构化日志和搜索能力]

### 告警策略
**告警级别**：[Warning、Critical、Emergency 分级]
**通知渠道**：[Slack、邮件、PagerDuty 集成]
**升级机制**：[值班轮转和升级策略]

## 安全与合规

### 安全自动化
**漏洞扫描**：[容器和依赖扫描]
**密钥管理**：[自动轮转和安全存储]
**网络安全**：[防火墙规则和网络策略]

### 合规自动化
**审计日志**：[完整的审计追踪创建]
**合规报告**：[自动化合规状态报告]
**策略执行**：[自动化策略合规检查]

---
**DevOps 自动化师**：[你的名字]
**基础设施日期**：[日期]
**部署**：全自动化，具备零停机能力
**监控**：全面的可观测性和告警已激活
```
