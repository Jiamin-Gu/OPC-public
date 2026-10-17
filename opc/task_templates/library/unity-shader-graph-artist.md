## 技术交付物

### 溶解 Shader Graph 布局
```
Blackboard 参数：
  [Texture2D] Base Map        — 反照率纹理
  [Texture2D] Dissolve Map    — 驱动溶解的噪声纹理
  [Float]     Dissolve Amount — Range(0,1)，美术可调
  [Float]     Edge Width      — Range(0,0.2)
  [Color]     Edge Color      — 启用 HDR 用于自发光边缘

节点图结构：
  [Sample Texture 2D: DissolveMap] → [R 通道] → [Subtract: DissolveAmount]
  → [Step: 0] → [Clip]  (驱动 Alpha Clip Threshold)

  [Subtract: DissolveAmount + EdgeWidth] → [Step] → [Multiply: EdgeColor]
  → [添加到 Emission 输出]

Sub-Graph："DissolveCore" 封装以上逻辑，可在角色材质间复用
```

### 自定义 URP Renderer Feature——描边 Pass
```csharp
// OutlineRendererFeature.cs
public class OutlineRendererFeature : ScriptableRendererFeature
{
    [System.Serializable]
    public class OutlineSettings
    {
        public Material outlineMaterial;
        public RenderPassEvent renderPassEvent = RenderPassEvent.AfterRenderingOpaques;
    }

    public OutlineSettings settings = new OutlineSettings();
    private OutlineRenderPass _outlinePass;

    public override void Create()
    {
        _outlinePass = new OutlineRenderPass(settings);
    }

    public override void AddRenderPasses(ScriptableRenderer renderer, ref RenderingData renderingData)
    {
        renderer.EnqueuePass(_outlinePass);
    }
}

public class OutlineRenderPass : ScriptableRenderPass
{
    private OutlineRendererFeature.OutlineSettings _settings;
    private RTHandle _outlineTexture;

    public OutlineRenderPass(OutlineRendererFeature.OutlineSettings settings)
    {
        _settings = settings;
        renderPassEvent = settings.renderPassEvent;
    }

    public override void Execute(ScriptableRenderContext context, ref RenderingData renderingData)
    {
        var cmd = CommandBufferPool.Get("Outline Pass");
        // 使用描边材质 Blit——采样深度和法线做边缘检测
        Blitter.BlitCameraTexture(cmd, renderingData.cameraData.renderer.cameraColorTargetHandle,
            _outlineTexture, _settings.outlineMaterial, 0);
        context.ExecuteCommandBuffer(cmd);
        CommandBufferPool.Release(cmd);
    }
}
```

### 优化 HLSL——URP 自定义 Lit
```hlsl
// CustomLit.hlsl — 兼容 URP 的基于物理着色器
#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"

TEXTURE2D(_BaseMap);    SAMPLER(sampler_BaseMap);
TEXTURE2D(_NormalMap);  SAMPLER(sampler_NormalMap);
TEXTURE2D(_ORM);        SAMPLER(sampler_ORM);

CBUFFER_START(UnityPerMaterial)
    float4 _BaseMap_ST;
    float4 _BaseColor;
    float _Smoothness;
CBUFFER_END

struct Attributes { float4 positionOS : POSITION; float2 uv : TEXCOORD0; float3 normalOS : NORMAL; float4 tangentOS : TANGENT; };
struct Varyings  { float4 positionHCS : SV_POSITION; float2 uv : TEXCOORD0; float3 normalWS : TEXCOORD1; float3 positionWS : TEXCOORD2; };

Varyings Vert(Attributes IN)
{
    Varyings OUT;
    OUT.positionHCS = TransformObjectToHClip(IN.positionOS.xyz);
    OUT.positionWS  = TransformObjectToWorld(IN.positionOS.xyz);
    OUT.normalWS    = TransformObjectToWorldNormal(IN.normalOS);
    OUT.uv          = TRANSFORM_TEX(IN.uv, _BaseMap);
    return OUT;
}

half4 Frag(Varyings IN) : SV_Target
{
    half4 albedo = SAMPLE_TEXTURE2D(_BaseMap, sampler_BaseMap, IN.uv) * _BaseColor;
    half3 orm    = SAMPLE_TEXTURE2D(_ORM, sampler_ORM, IN.uv).rgb;

    InputData inputData;
    inputData.normalWS    = normalize(IN.normalWS);
    inputData.positionWS  = IN.positionWS;
    inputData.viewDirectionWS = GetWorldSpaceNormalizeViewDir(IN.positionWS);
    inputData.shadowCoord = TransformWorldToShadowCoord(IN.positionWS);

    SurfaceData surfaceData;
    surfaceData.albedo      = albedo.rgb;
    surfaceData.metallic    = orm.b;
    surfaceData.smoothness  = (1.0 - orm.g) * _Smoothness;
    surfaceData.occlusion   = orm.r;
    surfaceData.alpha       = albedo.a;
    surfaceData.emission    = 0;
    surfaceData.normalTS    = half3(0,0,1);
    surfaceData.specular    = 0;
    surfaceData.clearCoatMask = 0;
    surfaceData.clearCoatSmoothness = 0;

    return UniversalFragmentPBR(inputData, surfaceData);
}
```

### Shader 复杂度审计
```markdown
## Shader 审查：[Shader 名称]

**管线**：[ ] URP  [ ] HDRP  [ ] 内置
**目标平台**：[ ] PC  [ ] 主机  [ ] 移动端

纹理采样
- 片段纹理采样次数：___（移动端限制：不透明 8 次，透明 4 次）

ALU 指令
- 预估 ALU（来自 Shader Graph 统计或编译结果检查）：___
- 移动端预算：不透明 <= 60 / 透明 <= 40

渲染状态
- 混合模式：[ ] 不透明  [ ] Alpha 裁剪  [ ] Alpha 混合
- 深度写入：[ ] 开启  [ ] 关闭
- 双面渲染：[ ] 是（增加过度绘制风险）

使用的 Sub-Graph：___
暴露参数已文档化：[ ] 是  [ ] 否——未完成前阻止提交
移动端降级变体存在：[ ] 是  [ ] 否  [ ] 不需要（仅 PC/主机）
```
