## 技术交付物

### 华为 VRP：接入交换机标准化配置

```
# VLAN 与接口
vlan batch 10 20 100
#
interface GigabitEthernet0/0/1
 description To-PC-Office
 port link-type access
 port default vlan 10
 stp edged-port enable          # 边缘端口，加快收敛；配合全局 stp bpdu-protection，收到 BPDU 立即 error-down 防环
#
interface GigabitEthernet0/0/24
 description To-Core-Uplink
 port link-type trunk
 port trunk allow-pass vlan 10 20 100
#
# 全局防环兜底
stp mode rstp
stp bpdu-protection
```

### 华为 VRP：OSPF 骨干配置

```
ospf 1 router-id 10.0.0.1
 area 0.0.0.0
  network 10.0.0.0 0.0.0.255
  authentication-mode md5 1 cipher Huawei@123   # 区域认证
#
interface GigabitEthernet0/0/24
 ospf network-type p2p          # 点到点，省去 DR/BDR 选举
 ospf timer hello 10
```

### 华三 Comware：链路聚合（对比 VRP 语法差异）

```
interface Bridge-Aggregation 1
 link-aggregation mode dynamic          # LACP 动态聚合
#
interface GigabitEthernet1/0/1
 port link-aggregation group 1
interface GigabitEthernet1/0/2
 port link-aggregation group 1
```

对应华为 VRP 写法（注意命令体系不同）：

```
interface Eth-Trunk1
 mode lacp-static
#
interface GigabitEthernet0/0/1
 eth-trunk 1
```

### 排障命令速查（华为 VRP）

```
display stp brief                    # 看端口角色/状态，定位环路
display ospf peer brief              # OSPF 邻居状态
display ip routing-table             # 路由表，查黑洞
display interface brief | include up # 快速看接口 up/down 和流量
display logbuffer                    # 设备日志，找 error-down 原因
```
