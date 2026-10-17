## 技术交付物

### SwiftUI 终端视图集成

```swift
import SwiftUI
import SwiftTerm

struct TerminalContainerView: View {
    @State private var terminal = SwiftTermController()
    @State private var fontSize: CGFloat = 14
    @State private var colorScheme: TerminalColorScheme = .solarizedDark

    var body: some View {
        VStack(spacing: 0) {
            // 工具栏
            TerminalToolbar(
                fontSize: $fontSize,
                colorScheme: $colorScheme,
                onClear: { terminal.clear() },
                onSearch: { terminal.startSearch() }
            )

            // 终端视图
            TerminalViewRepresentable(
                controller: terminal,
                fontSize: fontSize,
                colorScheme: colorScheme
            )
            .onAppear {
                terminal.startProcess(
                    executable: "/bin/zsh",
                    args: ["--login"],
                    environment: buildEnvironment()
                )
            }
            .onDisappear {
                terminal.terminateProcess()
            }
        }
    }

    private func buildEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["TERM"] = "xterm-256color"
        env["LANG"] = "en_US.UTF-8"
        env["COLORTERM"] = "truecolor"
        return env
    }
}

class SwiftTermController: ObservableObject {
    private var terminalView: LocalProcessTerminalView?
    private var process: Process?
    private let outputQueue = DispatchQueue(label: "terminal.output", qos: .userInteractive)

    func startProcess(executable: String, args: [String], environment: [String: String]) {
        guard let view = terminalView else { return }
        view.startProcess(
            executable: executable,
            args: args,
            environment: environment.map { "\($0.key)=\($0.value)" },
            execName: nil
        )
    }

    func clear() {
        // 发送 clear 转义序列，而不是执行命令
        terminalView?.send(txt: "\u{1b}[2J\u{1b}[H")
    }

    func terminateProcess() {
        process?.terminate()
        process = nil
    }
}
```

### 高频输出渲染合并

```swift
class RenderCoalescer {
    private var pendingLines: [TerminalLine] = []
    private var displayLink: CADisplayLink?
    private var isDirty = false
    private let lock = NSLock()

    /// 终端输出回调 —— 可以从任何线程调用
    func appendOutput(_ lines: [TerminalLine]) {
        lock.lock()
        pendingLines.append(contentsOf: lines)
        isDirty = true
        lock.unlock()
    }

    /// 绑定到屏幕刷新率，每帧最多渲染一次
    func startCoalescing(target: AnyObject, action: Selector) {
        displayLink = CADisplayLink(target: target, selector: action)
        displayLink?.add(to: .main, forMode: .common)
    }

    /// 在 displayLink 回调中调用
    func flushIfNeeded() -> [TerminalLine]? {
        lock.lock()
        defer { lock.unlock() }

        guard isDirty else { return nil }
        let lines = pendingLines
        pendingLines.removeAll(keepingCapacity: true)
        isDirty = false
        return lines
    }

    func stop() {
        displayLink?.invalidate()
        displayLink = nil
    }
}
```
