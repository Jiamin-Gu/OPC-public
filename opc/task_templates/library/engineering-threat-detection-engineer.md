## 技术交付物

### Sigma 检测规则

```yaml
# Sigma 规则：可疑的 PowerShell 编码命令执行
title: Suspicious PowerShell Encoded Command Execution
id: f3a8c5d2-7b91-4e2a-b6c1-9d4e8f2a1b3c
status: stable
level: high
description: |
  检测使用编码命令的 PowerShell 执行行为。这是攻击者常用的技术，
  用于混淆恶意载荷并绕过简单的命令行日志检测。
references:
  - https://attack.mitre.org/techniques/T1059/001/
  - https://attack.mitre.org/techniques/T1027/010/
author: Detection Engineering Team
date: 2025/03/15
modified: 2025/06/20
tags:
  - attack.execution
  - attack.t1059.001
  - attack.defense_evasion
  - attack.t1027.010
logsource:
  category: process_creation
  product: windows
detection:
  selection_parent:
    ParentImage|endswith:
      - '\cmd.exe'
      - '\wscript.exe'
      - '\cscript.exe'
      - '\mshta.exe'
      - '\wmiprvse.exe'
  selection_powershell:
    Image|endswith:
      - '\powershell.exe'
      - '\pwsh.exe'
    CommandLine|contains:
      - '-enc '
      - '-EncodedCommand'
      - '-ec '
      - 'FromBase64String'
  condition: selection_parent and selection_powershell
falsepositives:
  - 某些合法的 IT 自动化工具会使用编码命令进行部署
  - SCCM 和 Intune 可能使用编码 PowerShell 进行软件分发
  - 将已知合法的编码命令来源记录到白名单中
fields:
  - ParentImage
  - Image
  - CommandLine
  - User
  - Computer
```

### 编译为 Splunk SPL

```spl
| 可疑的 PowerShell 编码命令——从 Sigma 规则编译
index=windows sourcetype=WinEventLog:Sysmon EventCode=1
  (ParentImage="*\\cmd.exe" OR ParentImage="*\\wscript.exe"
   OR ParentImage="*\\cscript.exe" OR ParentImage="*\\mshta.exe"
   OR ParentImage="*\\wmiprvse.exe")
  (Image="*\\powershell.exe" OR Image="*\\pwsh.exe")
  (CommandLine="*-enc *" OR CommandLine="*-EncodedCommand*"
   OR CommandLine="*-ec *" OR CommandLine="*FromBase64String*")
| eval risk_score=case(
    ParentImage LIKE "%wmiprvse.exe", 90,
    ParentImage LIKE "%mshta.exe", 85,
    1=1, 70
  )
| where NOT match(CommandLine, "(?i)(SCCM|ConfigMgr|Intune)")
| table _time Computer User ParentImage Image CommandLine risk_score
| sort - risk_score
```

### 编译为 Microsoft Sentinel KQL

```kql
// 可疑的 PowerShell 编码命令——从 Sigma 规则编译
DeviceProcessEvents
| where Timestamp > ago(1h)
| where InitiatingProcessFileName in~ (
    "cmd.exe", "wscript.exe", "cscript.exe", "mshta.exe", "wmiprvse.exe"
  )
| where FileName in~ ("powershell.exe", "pwsh.exe")
| where ProcessCommandLine has_any (
    "-enc ", "-EncodedCommand", "-ec ", "FromBase64String"
  )
// 排除已知合法的自动化工具
| where ProcessCommandLine !contains "SCCM"
    and ProcessCommandLine !contains "ConfigMgr"
| extend RiskScore = case(
    InitiatingProcessFileName =~ "wmiprvse.exe", 90,
    InitiatingProcessFileName =~ "mshta.exe", 85,
    70
  )
| project Timestamp, DeviceName, AccountName,
    InitiatingProcessFileName, FileName, ProcessCommandLine, RiskScore
| sort by RiskScore desc
```

### MITRE ATT&CK 覆盖度评估模板

```markdown
# MITRE ATT&CK 检测覆盖度报告

**评估日期**：YYYY-MM-DD
**平台**：Windows 终端
**评估技术总数**：201
**检测覆盖度**：67/201 (33%)

## 按战术维度的覆盖度

| 战术 | 技术数 | 已覆盖 | 缺口 | 覆盖率 |
|------|--------|--------|------|--------|
| 初始访问 | 9 | 4 | 5 | 44% |
| 执行 | 14 | 9 | 5 | 64% |
| 持久化 | 19 | 8 | 11 | 42% |
| 权限提升 | 13 | 5 | 8 | 38% |
| 防御规避 | 42 | 12 | 30 | 29% |
| 凭证获取 | 17 | 7 | 10 | 41% |
| 发现 | 32 | 11 | 21 | 34% |
| 横向移动 | 9 | 4 | 5 | 44% |
| 信息收集 | 17 | 3 | 14 | 18% |
| 数据外泄 | 9 | 2 | 7 | 22% |
| 命令与控制 | 16 | 5 | 11 | 31% |
| 影响 | 14 | 3 | 11 | 21% |

## 关键缺口（最高优先级）
我们所在行业的威胁行为者正在使用但检测覆盖度为零的技术：

| 技术 ID | 技术名称 | 使用者 | 优先级 |
|---------|---------|--------|--------|
| T1003.001 | LSASS 内存转储 | APT29, FIN7 | 紧急 |
| T1055.012 | 进程镂空 | Lazarus, APT41 | 紧急 |
| T1071.001 | Web 协议 C2 | 多数 APT 组织 | 紧急 |
| T1562.001 | 禁用安全工具 | 勒索软件团伙 | 高 |
| T1486 | 数据加密破坏 | 所有勒索软件 | 高 |

## 检测路线图（下季度）
| Sprint | 目标覆盖技术 | 需编写规则数 | 所需数据源 |
|--------|-------------|-------------|-----------|
| S1 | T1003.001, T1055.012 | 4 | Sysmon (Event 10, 8) |
| S2 | T1071.001, T1071.004 | 3 | DNS 日志, 代理日志 |
| S3 | T1562.001, T1486 | 5 | EDR 遥测 |
| S4 | T1053.005, T1547.001 | 4 | Windows Security 日志 |
```

### 检测即代码 CI/CD 流水线

```yaml
# GitHub Actions：检测规则 CI/CD 流水线
name: Detection Engineering Pipeline

on:
  pull_request:
    paths: ['detections/**/*.yml']
  push:
    branches: [main]
    paths: ['detections/**/*.yml']

jobs:
  validate:
    name: 校验 Sigma 规则
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: 安装 sigma-cli
        run: pip install sigma-cli pySigma-backend-splunk pySigma-backend-microsoft365defender

      - name: 校验 Sigma 语法
        run: |
          find detections/ -name "*.yml" -exec sigma check {} \;

      - name: 检查必填字段
        run: |
          # 每条规则必须包含：title, id, level, tags (ATT&CK), falsepositives
          for rule in detections/**/*.yml; do
            for field in title id level tags falsepositives; do
              if ! grep -q "^${field}:" "$rule"; then
                echo "ERROR: $rule 缺少必填字段: $field"
                exit 1
              fi
            done
          done

      - name: 验证 ATT&CK 映射
        run: |
          # 每条规则必须映射到至少一个 ATT&CK 技术
          for rule in detections/**/*.yml; do
            if ! grep -q "attack\.t[0-9]" "$rule"; then
              echo "ERROR: $rule 没有 ATT&CK 技术映射"
              exit 1
            fi
          done

  compile:
    name: 编译到目标 SIEM
    needs: validate
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: 安装 sigma-cli 及后端
        run: |
          pip install sigma-cli \
            pySigma-backend-splunk \
            pySigma-backend-microsoft365defender \
            pySigma-backend-elasticsearch

      - name: 编译到 Splunk
        run: |
          sigma convert -t splunk -p sysmon \
            detections/**/*.yml > compiled/splunk/rules.conf

      - name: 编译到 Sentinel KQL
        run: |
          sigma convert -t microsoft365defender \
            detections/**/*.yml > compiled/sentinel/rules.kql

      - name: 编译到 Elastic EQL
        run: |
          sigma convert -t elasticsearch \
            detections/**/*.yml > compiled/elastic/rules.ndjson

      - uses: actions/upload-artifact@v4
        with:
          name: compiled-rules
          path: compiled/

  test:
    name: 使用样本日志测试
    needs: compile
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: 运行检测测试
        run: |
          # 每条规则应在 tests/ 中有对应的测试用例
          for rule in detections/**/*.yml; do
            rule_id=$(grep "^id:" "$rule" | awk '{print $2}')
            test_file="tests/${rule_id}.json"
            if [ ! -f "$test_file" ]; then
              echo "WARN: 规则 $rule_id ($rule) 没有测试用例"
            else
              echo "正在测试规则 $rule_id..."
              python scripts/test_detection.py \
                --rule "$rule" --test-data "$test_file"
            fi
          done

  deploy:
    name: 部署到 SIEM
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: compiled-rules

      - name: 部署到 Splunk
        run: |
          # 通过 Splunk REST API 推送编译后的规则
          curl -k -u "${{ secrets.SPLUNK_USER }}:${{ secrets.SPLUNK_PASS }}" \
            https://${{ secrets.SPLUNK_HOST }}:8089/servicesNS/admin/search/saved/searches \
            -d @compiled/splunk/rules.conf

      - name: 部署到 Sentinel
        run: |
          # 通过 Azure CLI 部署
          az sentinel alert-rule create \
            --resource-group ${{ secrets.AZURE_RG }} \
            --workspace-name ${{ secrets.SENTINEL_WORKSPACE }} \
            --alert-rule @compiled/sentinel/rules.kql
```

### 威胁狩猎 Playbook

```markdown
# 威胁狩猎：通过 LSASS 获取凭证

## 狩猎假设
拥有本地管理员权限的攻击者正在使用 Mimikatz、ProcDump 或直接 ntdll 调用
从 LSASS 进程内存中转储凭证，而我们当前的检测未能覆盖所有变种。

## MITRE ATT&CK 映射
- **T1003.001** — 操作系统凭证转储：LSASS 内存
- **T1003.003** — 操作系统凭证转储：NTDS

## 所需数据源
- Sysmon Event ID 10 (ProcessAccess) — 带可疑权限的 LSASS 访问
- Sysmon Event ID 7 (ImageLoaded) — 加载到 LSASS 的 DLL
- Sysmon Event ID 1 (ProcessCreate) — 带 LSASS 句柄的进程创建

## 狩猎查询

### 查询 1：直接 LSASS 访问（Sysmon Event 10）
```
index=windows sourcetype=WinEventLog:Sysmon EventCode=10
  TargetImage="*\\lsass.exe"
  GrantedAccess IN ("0x1010", "0x1038", "0x1fffff", "0x1410")
  NOT SourceImage IN (
    "*\\csrss.exe", "*\\lsm.exe", "*\\wmiprvse.exe",
    "*\\svchost.exe", "*\\MsMpEng.exe"
  )
| stats count by SourceImage GrantedAccess Computer User
| sort - count
```

### 查询 2：加载到 LSASS 的可疑模块
```
index=windows sourcetype=WinEventLog:Sysmon EventCode=7
  Image="*\\lsass.exe"
  NOT ImageLoaded IN ("*\\Windows\\System32\\*", "*\\Windows\\SysWOW64\\*")
| stats count values(ImageLoaded) as SuspiciousModules by Computer
```

## 预期结果
- **真正指标**：非系统进程以高权限访问掩码访问 LSASS、异常 DLL 加载到 LSASS
- **需要建基线的正常活动**：安全工具（EDR、杀毒软件）因保护目的访问 LSASS、凭证提供程序、SSO 代理

## 从狩猎到检测的转化
如果狩猎发现真正阳性或新的访问模式：
1. 创建覆盖发现的技术变种的 Sigma 规则
2. 将发现的合法工具添加到白名单
3. 通过检测即代码流水线提交规则
4. 使用 atomic red team 测试 T1003.001 进行验证
```

### 检测规则元数据目录 Schema

```yaml
# 检测目录条目——追踪规则生命周期和效能
rule_id: "f3a8c5d2-7b91-4e2a-b6c1-9d4e8f2a1b3c"
title: "Suspicious PowerShell Encoded Command Execution"
status: stable   # draft | testing | stable | deprecated
severity: high
confidence: medium  # low | medium | high

mitre_attack:
  tactics: [execution, defense_evasion]
  techniques: [T1059.001, T1027.010]

data_sources:
  required:
    - source: "Sysmon"
      event_ids: [1]
      status: collecting   # collecting | partial | not_collecting
    - source: "Windows Security"
      event_ids: [4688]
      status: collecting

performance:
  avg_daily_alerts: 3.2
  true_positive_rate: 0.78
  false_positive_rate: 0.22
  mean_time_to_triage: "4m"
  last_true_positive: "2025-05-12"
  last_validated: "2025-06-01"
  validation_method: "atomic_red_team"

allowlist:
  - pattern: "SCCM\\\\.*powershell.exe.*-enc"
    reason: "SCCM 软件部署使用编码命令"
    added: "2025-03-20"
    reviewed: "2025-06-01"

lifecycle:
  created: "2025-03-15"
  author: "detection-engineering-team"
  last_modified: "2025-06-20"
  review_due: "2025-09-15"
  review_cadence: quarterly
```
