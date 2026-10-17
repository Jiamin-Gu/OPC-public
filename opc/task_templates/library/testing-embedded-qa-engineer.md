## 技术交付物

### 固件单元测试框架（Unity + CMock）

```c
// test_sensor_parser.c
#include "unity.h"
#include "sensor_parser.h"

void setUp(void) {}
void tearDown(void) {}

void test_parse_valid_temperature(void)
{
    uint8_t raw[] = {0x01, 0x9A};  // 25.6°C
    float result = parse_temperature(raw, sizeof(raw));
    TEST_ASSERT_FLOAT_WITHIN(0.1f, 25.6f, result);
}

void test_parse_invalid_length_returns_nan(void)
{
    uint8_t raw[] = {0x01};
    float result = parse_temperature(raw, sizeof(raw));
    TEST_ASSERT_TRUE(isnan(result));
}

void test_parse_overflow_clamped(void)
{
    uint8_t raw[] = {0xFF, 0xFF};  // 超量程
    float result = parse_temperature(raw, sizeof(raw));
    TEST_ASSERT_EQUAL_FLOAT(TEMP_MAX, result);
}
```

### HIL 测试脚本（Python + PySerial + GPIO）

```python
import pytest
import serial
import RPi.GPIO as GPIO
import time

RESET_PIN = 17
DUT_SERIAL = "/dev/ttyUSB0"

@pytest.fixture
def dut():
    """复位设备并建立串口连接"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(RESET_PIN, GPIO.OUT)

    # 硬件复位
    GPIO.output(RESET_PIN, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(RESET_PIN, GPIO.HIGH)
    time.sleep(2)  # 等待启动

    ser = serial.Serial(DUT_SERIAL, 115200, timeout=5)
    yield ser
    ser.close()
    GPIO.cleanup()

def test_boot_message(dut):
    """验证设备启动后输出版本信息"""
    output = dut.read_until(b"READY\r\n", timeout=10)
    assert b"FW_VERSION" in output
    assert b"READY" in output

def test_sensor_read_command(dut):
    """发送读取指令，验证响应格式和范围"""
    dut.write(b"READ_TEMP\r\n")
    response = dut.readline().decode().strip()
    temp = float(response.split("=")[1])
    assert -40.0 <= temp <= 85.0, f"温度超范围: {temp}"

def test_power_cycle_recovery(dut):
    """验证掉电重启后数据不丢失"""
    # 写入配置
    dut.write(b"SET_THRESHOLD=30.0\r\n")
    assert b"OK" in dut.readline()

    # 掉电重启
    GPIO.output(RESET_PIN, GPIO.LOW)
    time.sleep(0.5)
    GPIO.output(RESET_PIN, GPIO.HIGH)
    time.sleep(2)

    # 验证配置保留
    dut.write(b"GET_THRESHOLD\r\n")
    response = dut.readline().decode().strip()
    assert "30.0" in response
```

### CI 嵌入式测试流水线（GitHub Actions + 自托管 Runner）

```yaml
name: Firmware CI
on: [push, pull_request]

jobs:
  unit-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build and run unit tests
        run: |
          cd tests/unit
          cmake -B build -DCMAKE_BUILD_TYPE=Debug
          cmake --build build
          ctest --test-dir build --output-on-failure

  integration-test:
    runs-on: [self-hosted, hil-runner]
    needs: unit-test
    steps:
      - uses: actions/checkout@v4
      - name: Flash firmware
        run: |
          idf.py build
          idf.py -p /dev/ttyUSB0 flash
      - name: Run HIL tests
        run: |
          pytest tests/hil/ -v --junitxml=results.xml
      - uses: actions/upload-artifact@v4
        with:
          name: test-results
          path: results.xml
```

### 量产测试报告模板

```
========================================
  量产测试报告
  产品: SENSOR-V2    SN: SN20260318001
  日期: 2026-03-18   测试站: ST-03
========================================
[PASS] 供电电流    : 52mA  (规格: <80mA)
[PASS] 时钟精度    : +1.2ppm (规格: ±10ppm)
[PASS] 温度传感器  : 25.3°C (参考: 25.1°C, 误差<0.5°C)
[PASS] Wi-Fi RSSI  : -42dBm (规格: >-60dBm)
[PASS] BLE TX Power: +4dBm  (规格: +3~+5dBm)
[PASS] Flash 自检  : CRC OK
[PASS] 序列号烧录  : SN20260318001 已写入
[PASS] 校准系数    : 已写入 NVS
========================================
  结果: PASS   耗时: 18.3s
========================================
```
