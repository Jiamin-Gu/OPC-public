## 技术交付物

### 空间 UI 布局系统

```javascript
class SpatialUILayout {
  constructor(userHeight = 1.65) {
    // 舒适区定义（相对于用户头部）
    this.comfortZone = {
      minDistance: 0.8,   // 最近距离（米）
      maxDistance: 3.0,   // 最远距离
      optimalDistance: 1.5, // 最佳阅读距离
      horizontalFOV: 60,  // 水平舒适视角（度）
      verticalUp: 20,     // 向上舒适角度
      verticalDown: 12,   // 向下舒适角度
    };
    this.userHeight = userHeight;
    this.panels = [];
  }

  /**
   * 将面板放置在舒适区内的指定方位
   * @param {string} zone - 空间区域: 'center'|'left'|'right'|'above'|'below'
   * @param {object} size - { width, height } 面板尺寸（米）
   * @param {string} anchor - 锚定模式: 'world'|'body'|'head'
   */
  placePanel(zone, size, anchor = 'body') {
    const position = this.calculatePosition(zone);
    const rotation = this.calculateRotation(position);

    // 验证舒适度约束
    const comfort = this.validateComfort(position, size);
    if (!comfort.valid) {
      console.warn(`布局警告: ${comfort.reason}`);
      // 自动修正到最近的舒适位置
      position.copy(comfort.suggestedPosition);
    }

    const panel = {
      position, rotation, size, anchor, zone,
      minTargetSize: 0.02, // 最小可交互目标 2cm
      fontSize: this.calculateFontSize(position),
    };
    this.panels.push(panel);
    return panel;
  }

  calculatePosition(zone) {
    const d = this.comfortZone.optimalDistance;
    const eyeHeight = this.userHeight - 0.12; // 眼睛约在头顶下12cm
    const positions = {
      center: { x: 0, y: eyeHeight, z: -d },
      left:   { x: -d * 0.7, y: eyeHeight, z: -d * 0.7 },
      right:  { x: d * 0.7, y: eyeHeight, z: -d * 0.7 },
      above:  { x: 0, y: eyeHeight + 0.4, z: -d },
      below:  { x: 0, y: eyeHeight - 0.3, z: -d * 0.9 },
    };
    const p = positions[zone] || positions.center;
    return new THREE.Vector3(p.x, p.y, p.z);
  }

  calculateFontSize(position) {
    // 基于距离计算等效字号，保证视觉角度一致
    const distance = position.length();
    // 24pt 在 1.5m 处的视觉角度作为基准
    const baseAngle = 0.024 / 1.5; // tan(视角) ≈ 物理尺寸/距离
    return baseAngle * distance; // 返回物理尺寸（米）
  }

  validateComfort(position, size) {
    const distance = position.length();
    const cz = this.comfortZone;

    if (distance < cz.minDistance) {
      return {
        valid: false,
        reason: `距离 ${distance.toFixed(2)}m 过近，最低 ${cz.minDistance}m`,
        suggestedPosition: position.normalize().multiplyScalar(cz.minDistance),
      };
    }

    // 计算水平角度
    const hAngle = Math.abs(Math.atan2(position.x, -position.z)) * 180 / Math.PI;
    if (hAngle > cz.horizontalFOV / 2) {
      return {
        valid: false,
        reason: `水平角度 ${hAngle.toFixed(1)}° 超出舒适区 ±${cz.horizontalFOV/2}°`,
        suggestedPosition: position, // 简化处理
      };
    }

    return { valid: true };
  }
}
```

### 多模态输入状态机

```javascript
const InputModes = {
  GAZE_DWELL:  'gaze_dwell',   // 注视停留
  GAZE_PINCH:  'gaze_pinch',   // 注视+捏合
  DIRECT_TOUCH: 'direct_touch', // 直接触摸
  RAY_POINTER: 'ray_pointer',  // 射线指向
  VOICE:       'voice',         // 语音指令
};

class MultimodalInputManager {
  constructor() {
    this.activeMode = null;
    this.fallbackChain = [
      InputModes.DIRECT_TOUCH,
      InputModes.GAZE_PINCH,
      InputModes.RAY_POINTER,
      InputModes.GAZE_DWELL,
    ];
    this.dwellDuration = 800; // 注视停留确认时间（ms）
    this.dwellTimer = null;
  }

  detectAvailableModes(xrSession) {
    const available = [];
    if (xrSession.inputSources?.some(s => s.hand)) {
      available.push(InputModes.DIRECT_TOUCH, InputModes.GAZE_PINCH);
    }
    if (xrSession.inputSources?.some(s => s.gamepad)) {
      available.push(InputModes.RAY_POINTER);
    }
    // 注视停留始终可用作最终回退
    available.push(InputModes.GAZE_DWELL);
    return available;
  }

  selectBestMode(available, context) {
    // 近距离交互优先直接触摸，远距离优先射线
    if (context.targetDistance < 0.6 &&
        available.includes(InputModes.DIRECT_TOUCH)) {
      return InputModes.DIRECT_TOUCH;
    }
    // 按优先级链选择
    for (const mode of this.fallbackChain) {
      if (available.includes(mode)) return mode;
    }
    return InputModes.GAZE_DWELL;
  }
}
```
