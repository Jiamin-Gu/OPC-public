## 技术交付物

### 串口通信工作线程模板

```cpp
// SerialWorker.h —— 跑在独立 QThread 里
class SerialWorker : public QObject {
    Q_OBJECT
public:
    explicit SerialWorker(QObject *parent = nullptr);
public slots:
    void open(const QString &portName, qint32 baudRate);
    void close();
    void sendFrame(const QByteArray &frame);
signals:
    void frameReceived(const QByteArray &payload);
    void errorOccurred(const QString &msg);
    void connectionLost();
private slots:
    void onReadyRead();
    void onErrorOccurred(QSerialPort::SerialPortError err);
private:
    QSerialPort *port_ = nullptr;
    QByteArray rxBuffer_;  // 粘包/分包缓冲
    void parseFrames();    // 状态机式解析
};

// 主线程使用：
auto *thread = new QThread(this);
auto *worker = new SerialWorker;
worker->moveToThread(thread);
connect(thread, &QThread::finished, worker, &QObject::deleteLater);
connect(this, &MainWindow::openPortRequested, worker, &SerialWorker::open);
connect(worker, &SerialWorker::frameReceived, this, &MainWindow::onFrameReceived);
thread->start();
```

### Modbus RTU CRC16 校验

```cpp
quint16 crc16Modbus(const QByteArray &data) {
    quint16 crc = 0xFFFF;
    for (char c : data) {
        crc ^= static_cast<quint8>(c);
        for (int i = 0; i < 8; ++i) {
            crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
        }
    }
    return crc;  // 注意 Modbus 是低字节在前
}
```

### 自动重连定时器

```cpp
// 串口断开后每 2s 重试，避免 UI 假死
void DeviceManager::onConnectionLost() {
    emit statusChanged(tr("连接已断开，2 秒后重试..."));
    if (!reconnectTimer_) {
        reconnectTimer_ = new QTimer(this);
        reconnectTimer_->setSingleShot(true);
        connect(reconnectTimer_, &QTimer::timeout,
                this, &DeviceManager::tryReconnect);
    }
    reconnectTimer_->start(2000);
}
```

### QCustomPlot 实时滚动曲线（100kHz 级）

```cpp
plot_->addGraph();
plot_->graph(0)->setAdaptiveSampling(true);  // 关键：抽稀
plot_->setOpenGl(true);                      // 关键：OpenGL 加速

// 数据来了：
void Window::onSampleBatch(const QVector<double> &x, const QVector<double> &y) {
    plot_->graph(0)->addData(x, y, /*alreadySorted=*/true);
    // 仅保留最近 10s 的数据，避免内存爆炸
    plot_->graph(0)->data()->removeBefore(latestX_ - 10.0);
    plot_->xAxis->setRange(latestX_ - 10.0, latestX_);
    plot_->replot(QCustomPlot::rpQueuedReplot);  // 不立即重绘，合并请求
}
```

### CMakeLists.txt 模板（Qt 6）

```cmake
cmake_minimum_required(VERSION 3.16)
project(MyHostApp VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

find_package(Qt6 6.5 REQUIRED COMPONENTS
    Widgets SerialPort SerialBus Charts Network Sql)

qt_add_executable(MyHostApp
    src/main.cpp
    src/MainWindow.cpp src/MainWindow.h src/MainWindow.ui
    src/SerialWorker.cpp src/SerialWorker.h
    resources/app.qrc
)

target_link_libraries(MyHostApp PRIVATE
    Qt6::Widgets Qt6::SerialPort Qt6::SerialBus
    Qt6::Charts Qt6::Network Qt6::Sql)

# 国际化
qt_add_translations(MyHostApp TS_FILES translations/zh_CN.ts)
```
