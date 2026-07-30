import SwiftUI
import AppKit

struct RunHomeView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(t(model.language, "运行", "Run"))
                .font(.title2)
                .fontWeight(.semibold)
            Text(t(model.language, "当前页面内置一个运行面板；点击“多开运行窗口”可继续并行打开更多运行窗口。", "This page includes one embedded runner; click \"Open Additional Run Window\" to run more scripts in parallel."))
                .font(.footnote)
                .foregroundStyle(.secondary)
            if model.scripts.isEmpty {
                Text(t(model.language, "plans/ 目录下没有脚本", "No scripts found in plans/"))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                Text(t(model.language, "可用脚本数: \(model.scripts.count)", "Available Scripts: \(model.scripts.count)"))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Divider()
            RunnerView(
                runner: model.runnerA,
                lang: model.language,
                scripts: model.scripts.map(\.name),
                deviceOptions: model.availableDevices,
                refreshScripts: model.refreshScripts,
                refreshDevices: model.refreshADBAndDevices(adbInput:),
                onDeviceChanged: model.rememberLastDevice(_:),
                resolveScriptURL: model.scriptURL(named:),
                onSelectionChanged: { slot, script in
                    model.rememberRunnerScript(slot: slot, name: script)
                },
                openAdditionalRun: {
                    openWindow(id: "run-window", value: buildRunWindowValue())
                }
            )
            Spacer()
        }
        .padding(12)
    }
}

final class RunWindowCloseDelegate: NSObject, NSWindowDelegate {
    var runner: RunnerModel
    weak var originalDelegate: NSWindowDelegate?

    init(runner: RunnerModel) {
        self.runner = runner
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if let originalResult = originalDelegate?.windowShouldClose?(sender), !originalResult {
            return false
        }
        guard runner.isRunning else {
            return true
        }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "脚本正在运行"
        alert.informativeText = "确认关闭窗口吗？确认后会停止正在运行的脚本。"
        alert.addButton(withTitle: "确认关闭")
        alert.addButton(withTitle: "取消")
        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            runner.stop()
            return true
        }
        return false
    }
}

struct RunWindowCloseGuard: NSViewRepresentable {
    @ObservedObject var runner: RunnerModel

    final class Coordinator {
        weak var window: NSWindow?
        var delegate: RunWindowCloseDelegate?
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            attachDelegateIfNeeded(view: view, context: context)
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async {
            attachDelegateIfNeeded(view: nsView, context: context)
            context.coordinator.delegate?.runner = runner
        }
    }

    private func attachDelegateIfNeeded(view: NSView, context: Context) {
        guard let window = view.window else { return }
        if context.coordinator.window !== window {
            let delegate = RunWindowCloseDelegate(runner: runner)
            delegate.originalDelegate = window.delegate
            window.delegate = delegate
            context.coordinator.window = window
            context.coordinator.delegate = delegate
        }
    }
}

struct WindowAspectRatioGuard: NSViewRepresentable {
    let ratio: CGSize

    final class Coordinator {
        weak var window: NSWindow?
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            attachAspectRatio(view: view, context: context)
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async {
            attachAspectRatio(view: nsView, context: context)
        }
    }

    private func attachAspectRatio(view: NSView, context: Context) {
        guard let window = view.window else { return }
        if context.coordinator.window !== window {
            context.coordinator.window = window
        }
        window.contentAspectRatio = NSSize(width: ratio.width, height: ratio.height)
    }
}

struct WindowTitleGuard: NSViewRepresentable {
    let title: String

    final class Coordinator {
        weak var window: NSWindow?
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            updateWindowTitle(view: view, context: context)
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async {
            updateWindowTitle(view: nsView, context: context)
        }
    }

    private func updateWindowTitle(view: NSView, context: Context) {
        guard let window = view.window else { return }
        context.coordinator.window = window
        if window.title != title {
            window.title = title
        }
    }
}

final class MainWindowCoordinator {
    static let shared = MainWindowCoordinator()

    private var reopenAction: (() -> Void)?

    private init() {}

    func registerReopenAction(_ action: @escaping () -> Void) {
        reopenAction = action
    }

    var mainWindow: NSWindow? {
        NSApp.windows.first(where: { $0.identifier == mainWindowIdentifier })
    }

    var isMainWindowVisible: Bool {
        guard let window = mainWindow else { return false }
        return window.isVisible && !window.isMiniaturized
    }

    func ensureMainWindowIsVisible() {
        if let window = mainWindow {
            if window.isMiniaturized {
                window.deminiaturize(nil)
            }
            window.makeKeyAndOrderFront(nil)
        } else {
            reopenAction?()
            DispatchQueue.main.async {
                if let window = self.mainWindow {
                    if window.isMiniaturized {
                        window.deminiaturize(nil)
                    }
                    window.makeKeyAndOrderFront(nil)
                }
            }
        }
        NSApp.activate(ignoringOtherApps: true)
    }
}

struct MainWindowOpenActionBinder: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Color.clear
            .frame(width: 0, height: 0)
            .onAppear {
                MainWindowCoordinator.shared.registerReopenAction {
                    openWindow(id: mainWindowSceneID)
                }
            }
    }
}

struct WindowIdentifierGuard: NSViewRepresentable {
    let identifier: NSUserInterfaceItemIdentifier

    final class Coordinator {
        weak var window: NSWindow?
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            attachIdentifier(view: view, context: context)
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async {
            attachIdentifier(view: nsView, context: context)
        }
    }

    private func attachIdentifier(view: NSView, context: Context) {
        guard let window = view.window else { return }
        context.coordinator.window = window
        if window.identifier != identifier {
            window.identifier = identifier
        }
    }
}

struct RunWindowView: View {
    let windowID: String
    let initialScript: String?
    @EnvironmentObject private var model: AppModel
    @StateObject private var runner: RunnerModel

    init(windowID: String, initialScript: String? = nil) {
        self.windowID = windowID
        self.initialScript = initialScript
        _runner = StateObject(wrappedValue: RunnerModel(slotName: "Run"))
    }

    private var runWindowTitle: String {
        let deviceName = runner.device.trimmingCharacters(in: .whitespacesAndNewlines)
        return deviceName.isEmpty ? t(model.language, "运行", "Run") : "\(t(model.language, "运行", "Run")) - \(deviceName)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(t(model.language, "运行窗口", "Run Window"))
                    .font(.headline)
                Text(windowID.prefix(8))
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                Spacer()
                Text(model.adbStatusMessage)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            RunnerView(
                runner: runner,
                lang: model.language,
                scripts: model.scripts.map(\.name),
                deviceOptions: model.availableDevices,
                refreshScripts: model.refreshScripts,
                refreshDevices: model.refreshADBAndDevices(adbInput:),
                onDeviceChanged: model.rememberLastDevice(_:),
                resolveScriptURL: model.scriptURL(named:),
                onSelectionChanged: { _, _ in },
                openAdditionalRun: nil
            )
        }
        .padding(12)
        .background(
            ZStack {
                RunWindowCloseGuard(runner: runner)
                WindowAspectRatioGuard(ratio: CGSize(width: 760, height: 460))
                WindowTitleGuard(title: runWindowTitle)
            }
            .frame(width: 0, height: 0)
        )
        .onAppear {
            model.refreshScripts()
            if runner.selectedScript.isEmpty {
                if let initialScript, model.scripts.contains(where: { $0.name == initialScript }) {
                    runner.selectedScript = initialScript
                } else {
                    runner.selectedScript = model.selectedScriptName
                }
            }
            runner.device = model.preferredDeviceForCurrentList(current: runner.device)
        }
        .onChange(of: model.availableDevices) { _ in
            runner.device = model.preferredDeviceForCurrentList(current: runner.device)
        }
    }
}
