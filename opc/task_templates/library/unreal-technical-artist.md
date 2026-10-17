## 技术交付物

### Material Function——三平面映射
```
Material Function：MF_TriplanarMapping
输入：
  - Texture (Texture2D) — 要投影的纹理
  - BlendSharpness (Scalar, 默认 4.0) — 控制投影混合柔软度
  - Scale (Scalar, 默认 1.0) — 世界空间平铺大小

实现：
  WorldPosition → 乘以 Scale
  AbsoluteWorldNormal → Power(BlendSharpness) → Normalize → 混合权重 (X, Y, Z)
  SampleTexture(XY 平面) * BlendWeights.Z +
  SampleTexture(XZ 平面) * BlendWeights.Y +
  SampleTexture(YZ 平面) * BlendWeights.X
  → 输出：混合颜色、混合法线

用法：拖入任何世界材质。适用于岩石、悬崖、地形混合。
注意：比 UV 映射多 3 倍纹理采样——仅在 UV 接缝可见时使用。
```

### Niagara 系统——地面撞击爆发
```
系统类型：CPU 模拟（< 50 粒子）
发射器：Burst — 生成时 15-25 粒子，0 循环

模块：
  初始化粒子：
    生命周期：Uniform(0.3, 0.6)
    缩放：Uniform(0.5, 1.5)
    颜色：由表面材质参数驱动（泥土/石头/草地由 Material ID 决定）

  初始速度：
    锥形方向向上，45 度扩散
    速度：Uniform(150, 350) cm/s

  重力：-980 cm/s²

  阻力：0.8（摩擦力减缓水平扩散）

  缩放颜色/不透明度：
    淡出曲线：生命周期内线性 1.0 → 0.0

渲染器：
  Sprite 渲染器
  纹理：T_Particle_Dirt_Atlas（4x4 帧动画）
  混合模式：半透明——预算：爆发峰值最多 3 层过度绘制

可扩展性：
  高：25 粒子，完整纹理动画
  中：15 粒子，静态精灵
  低：5 粒子，无纹理动画
```

### PCG 图——森林填充
```
PCG 图：PCG_ForestPopulation

输入：Landscape Surface Sampler
  → 密度：每 10m² 0.8
  → 法线过滤：坡度 < 25°（排除陡峭地形）

变换点：
  → 位置抖动：±1.5m XY, 0 Z
  → 随机旋转：仅 Yaw 0-360°
  → 缩放变化：Uniform(0.8, 1.3)

密度过滤：
  → 泊松盘最小间距：2.0m（防止重叠）
  → 生物群落密度重映射：乘以生物群落密度纹理采样

排除区域：
  → 道路样条缓冲：5m 排除
  → 玩家路径缓冲：3m 排除
  → 手工放置 Actor 排除半径：10m

静态网格生成器：
  → 权重：橡树 (40%)、松树 (35%)、白桦 (20%)、枯树 (5%)
  → 所有网格：启用 Nanite
  → 剔除距离：60,000 cm

暴露给关卡的参数：
  - GlobalDensityMultiplier (0.0-2.0)
  - MinSeparationDistance (1.0-5.0m)
  - EnableRoadExclusion (bool)
```

### Shader 复杂度审计（Unreal）
```markdown
## 材质审查：[材质名称]

**着色模型**：[ ] DefaultLit  [ ] Unlit  [ ] Subsurface  [ ] Custom
**域**：[ ] Surface  [ ] Post Process  [ ] Decal

指令数（来自材质编辑器 Stats 窗口）
  Base Pass 指令数：___
  预算：< 200（移动端）、< 400（主机）、< 800（PC）

纹理采样
  总采样数：___
  预算：< 8（移动端）、< 16（主机）

Static Switch
  数量：___（每个使排列翻倍——每次添加需审批）

使用的 Material Function：___
Material Instance：[ ] 所有变体通过 MI  [ ] 直接修改了主材质——阻止提交
Quality Switch 层级已定义：[ ] 高  [ ] 中  [ ] 低
```

### Niagara 可扩展性配置
```
Niagara Scalability Asset：NS_ImpactDust_Scalability

效果类型 → Impact（触发剔除距离评估）

高画质（PC/主机高端）：
  最大活跃系统数：10
  每系统最大粒子数：50

中画质（主机基础版 / 中端 PC）：
  最大活跃系统数：6
  每系统最大粒子数：25
  → 剔除：距相机 > 30m 的系统

低画质（移动端 / 主机性能模式）：
  最大活跃系统数：3
  每系统最大粒子数：10
  → 剔除：距相机 > 15m 的系统
  → 禁用纹理动画

重要性处理器：NiagaraSignificanceHandlerDistance
  （越近 = 重要性越高 = 维持更高画质）
```
