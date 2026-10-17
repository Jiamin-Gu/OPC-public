## 技术交付物

### FMOD 事件命名规范
```
# 事件路径结构
event:/[类别]/[子类别]/[事件名]

# 示例
event:/SFX/Player/Footstep_Concrete
event:/SFX/Player/Footstep_Grass
event:/SFX/Weapons/Gunshot_Pistol
event:/SFX/Environment/Waterfall_Loop
event:/Music/Combat/Intensity_Low
event:/Music/Combat/Intensity_High
event:/Music/Exploration/Forest_Day
event:/UI/Button_Click
event:/UI/Menu_Open
event:/VO/NPC/[CharacterID]/[LineID]
```

### 音频集成——Unity/FMOD
```csharp
public class AudioManager : MonoBehaviour
{
    // 单例访问模式——仅适用于真正的全局音频状态
    public static AudioManager Instance { get; private set; }

    [SerializeField] private FMODUnity.EventReference _footstepEvent;
    [SerializeField] private FMODUnity.EventReference _musicEvent;

    private FMOD.Studio.EventInstance _musicInstance;

    private void Awake()
    {
        if (Instance != null) { Destroy(gameObject); return; }
        Instance = this;
    }

    public void PlayOneShot(FMODUnity.EventReference eventRef, Vector3 position)
    {
        FMODUnity.RuntimeManager.PlayOneShot(eventRef, position);
    }

    public void StartMusic(string state)
    {
        _musicInstance = FMODUnity.RuntimeManager.CreateInstance(_musicEvent);
        _musicInstance.setParameterByName("CombatIntensity", 0f);
        _musicInstance.start();
    }

    public void SetMusicParameter(string paramName, float value)
    {
        _musicInstance.setParameterByName(paramName, value);
    }

    public void StopMusic(bool fadeOut = true)
    {
        _musicInstance.stop(fadeOut
            ? FMOD.Studio.STOP_MODE.ALLOWFADEOUT
            : FMOD.Studio.STOP_MODE.IMMEDIATE);
        _musicInstance.release();
    }
}
```

### 自适应音乐参数架构
```markdown
## 音乐系统参数

### CombatIntensity（0.0 – 1.0）
- 0.0 = 附近没有敌人——仅播放探索层
- 0.3 = 敌人警戒状态——打击乐加入
- 0.6 = 战斗中——完整编曲
- 1.0 = Boss 战 / 危急状态——最高强度

**数据来源**：AI 威胁等级聚合脚本
**更新频率**：每 0.5 秒（通过 lerp 平滑）
**过渡方式**：量化到最近的节拍边界

### TimeOfDay（0.0 – 1.0）
- 控制室外环境音混合：白天鸟鸣 → 黄昏虫声 → 夜间风声
**数据来源**：游戏时钟系统
**更新频率**：每 5 秒

### PlayerHealth（0.0 – 1.0）
- 低于 0.2 时：非 UI 总线全部增加低通滤波
**数据来源**：玩家生命值组件
**更新频率**：生命值变化事件触发
```

### 音频预算规格
```markdown
# 音频性能预算——[项目名称]

## 发声数
| 平台   | 最大发声数 | 虚拟发声数 |
|--------|-----------|-----------|
| PC     | 64        | 256       |
| 主机   | 48        | 128       |
| 移动端 | 24        | 64        |

## 内存预算
| 类别     | 预算   | 格式   | 策略         |
|----------|--------|--------|-------------|
| 音效池   | 32 MB  | ADPCM  | 解压到内存   |
| 音乐     | 8 MB   | Vorbis | 流式播放     |
| 环境音   | 12 MB  | Vorbis | 流式播放     |
| 语音     | 4 MB   | Vorbis | 流式播放     |

## CPU 预算
- FMOD DSP：每帧不超过 1.5ms（在最低目标硬件上测量）
- 空间音频射线检测：每帧最多 4 次（跨帧分摊）

## 事件优先级层级
| 优先级    | 类型            | 抢占模式     |
|----------|-----------------|-------------|
| 0（最高） | UI、玩家语音     | 永不被抢占   |
| 1        | 玩家音效         | 抢占最安静的 |
| 2        | 战斗音效         | 抢占最远的   |
| 3（最低） | 环境音、植被     | 抢占最早的   |
```

### 空间音频方案
```markdown
## 3D 音频配置

### 衰减
- 最小距离：[X]m（满音量）
- 最大距离：[Y]m（完全静音）
- 衰减曲线：对数（写实风格）/ 线性（风格化）——按项目指定

### 遮挡
- 方式：从听者向音源原点做射线检测
- 参数："Occlusion"（0=无遮挡，1=完全遮挡）
- 完全遮挡时低通截止频率：800Hz
- 每帧最大射线检测次数：4（跨帧轮询更新）

### 混响区域
| 区域类型   | 预延迟 | 衰减时间 | 湿声比例 |
|-----------|--------|---------|---------|
| 室外      | 20ms   | 0.8s    | 15%     |
| 室内      | 30ms   | 1.5s    | 35%     |
| 洞穴      | 50ms   | 3.5s    | 60%     |
| 金属房间  | 15ms   | 1.0s    | 45%     |
```
