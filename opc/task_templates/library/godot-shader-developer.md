## 技术交付物

### 2D CanvasItem Shader——精灵描边
```glsl
shader_type canvas_item;

uniform vec4 outline_color : source_color = vec4(0.0, 0.0, 0.0, 1.0);
uniform float outline_width : hint_range(0.0, 10.0) = 2.0;

void fragment() {
    vec4 base_color = texture(TEXTURE, UV);

    // 在 outline_width 距离处采样 8 个邻居
    vec2 texel = TEXTURE_PIXEL_SIZE * outline_width;
    float alpha = 0.0;
    alpha = max(alpha, texture(TEXTURE, UV + vec2(texel.x, 0.0)).a);
    alpha = max(alpha, texture(TEXTURE, UV + vec2(-texel.x, 0.0)).a);
    alpha = max(alpha, texture(TEXTURE, UV + vec2(0.0, texel.y)).a);
    alpha = max(alpha, texture(TEXTURE, UV + vec2(0.0, -texel.y)).a);
    alpha = max(alpha, texture(TEXTURE, UV + vec2(texel.x, texel.y)).a);
    alpha = max(alpha, texture(TEXTURE, UV + vec2(-texel.x, texel.y)).a);
    alpha = max(alpha, texture(TEXTURE, UV + vec2(texel.x, -texel.y)).a);
    alpha = max(alpha, texture(TEXTURE, UV + vec2(-texel.x, -texel.y)).a);

    // 邻居有 alpha 但当前像素没有的地方画描边
    vec4 outline = outline_color * vec4(1.0, 1.0, 1.0, alpha * (1.0 - base_color.a));
    COLOR = base_color + outline;
}
```

### 3D Spatial Shader——溶解效果
```glsl
shader_type spatial;

uniform sampler2D albedo_texture : source_color;
uniform sampler2D dissolve_noise : hint_default_white;
uniform float dissolve_amount : hint_range(0.0, 1.0) = 0.0;
uniform float edge_width : hint_range(0.0, 0.2) = 0.05;
uniform vec4 edge_color : source_color = vec4(1.0, 0.4, 0.0, 1.0);

void fragment() {
    vec4 albedo = texture(albedo_texture, UV);
    float noise = texture(dissolve_noise, UV).r;

    // 裁剪溶解阈值以下的像素
    if (noise < dissolve_amount) {
        discard;
    }

    ALBEDO = albedo.rgb;

    // 在溶解前沿添加自发光边缘
    float edge = step(noise, dissolve_amount + edge_width);
    EMISSION = edge_color.rgb * edge * 3.0;  // * 3.0 用于 HDR 冲击力
    METALLIC = 0.0;
    ROUGHNESS = 0.8;
}
```

### 3D Spatial Shader——水面
```glsl
shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_back;

uniform sampler2D normal_map_a : hint_normal;
uniform sampler2D normal_map_b : hint_normal;
uniform float wave_speed : hint_range(0.0, 2.0) = 0.3;
uniform float wave_scale : hint_range(0.1, 10.0) = 2.0;
uniform vec4 shallow_color : source_color = vec4(0.1, 0.5, 0.6, 0.8);
uniform vec4 deep_color : source_color = vec4(0.02, 0.1, 0.3, 1.0);
uniform float depth_fade_distance : hint_range(0.1, 10.0) = 3.0;

void fragment() {
    vec2 time_offset_a = vec2(TIME * wave_speed * 0.7, TIME * wave_speed * 0.4);
    vec2 time_offset_b = vec2(-TIME * wave_speed * 0.5, TIME * wave_speed * 0.6);

    vec3 normal_a = texture(normal_map_a, UV * wave_scale + time_offset_a).rgb;
    vec3 normal_b = texture(normal_map_b, UV * wave_scale + time_offset_b).rgb;
    NORMAL_MAP = normalize(normal_a + normal_b);

    // 基于深度的颜色混合（需要 Forward+ / Mobile 渲染器的 DEPTH_TEXTURE）
    // 在 Compatibility 渲染器中：移除深度混合，使用固定的 shallow_color
    float depth_blend = clamp(FRAGCOORD.z / depth_fade_distance, 0.0, 1.0);
    vec4 water_color = mix(shallow_color, deep_color, depth_blend);

    ALBEDO = water_color.rgb;
    ALPHA = water_color.a;
    METALLIC = 0.0;
    ROUGHNESS = 0.05;
    SPECULAR = 0.9;
}
```

### 全屏后处理（CompositorEffect——Forward+）
```gdscript
# post_process_effect.gd — 必须继承 CompositorEffect
@tool
extends CompositorEffect

func _init() -> void:
    effect_callback_type = CompositorEffect.EFFECT_CALLBACK_TYPE_POST_TRANSPARENT

func _render_callback(effect_callback_type: int, render_data: RenderData) -> void:
    var render_scene_buffers := render_data.get_render_scene_buffers()
    if not render_scene_buffers:
        return

    var size := render_scene_buffers.get_internal_size()
    if size.x == 0 or size.y == 0:
        return

    # 使用 RenderingDevice 调度计算着色器
    var rd := RenderingServer.get_rendering_device()
    # ... 以屏幕纹理作为输入/输出调度计算着色器
    # 完整实现见 Godot 文档：CompositorEffect + RenderingDevice
```

### Shader 性能审计
```markdown
## Godot Shader 审查：[效果名称]

**Shader 类型**：[ ] canvas_item  [ ] spatial  [ ] particles
**目标渲染器**：[ ] Forward+  [ ] Mobile  [ ] Compatibility

纹理采样（片元阶段）
  数量：___（移动端预算：不透明材质每片元 ≤ 6 次）

检查器暴露的 Uniform
  [ ] 所有 uniform 都有提示（hint_range、source_color、hint_normal 等）
  [ ] shader 体内无魔法数字

Discard/Alpha 裁切
  [ ] 不透明 spatial shader 中使用了 discard？——标记：移动端转为 Alpha Scissor
  [ ] canvas_item 的 alpha 仅通过 COLOR.a 处理？

使用了 SCREEN_TEXTURE？
  [ ] 是——触发帧缓冲区拷贝。对此效果是否值得？
  [ ] 否

动态循环？
  [ ] 是——验证移动端上循环次数是常量或有上界
  [ ] 否

Compatibility 渲染器安全？
  [ ] 是  [ ] 否——在 shader 注释头中记录所需渲染器
```
