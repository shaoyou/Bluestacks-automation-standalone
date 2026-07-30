import SwiftUI
import AppKit

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow
    @State private var selectedSection: MainSection? = .run
    @State private var showNewScriptDialog = false
    @State private var newScriptName = ""

    var body: some View {
        NavigationSplitView {
            List(MainSection.allCases, selection: $selectedSection) { section in
                Text(section.title(model.language))
                    .tag(section)
            }
            .navigationTitle("BSManager")
        } detail: {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("BSManager v\(appVersion)")
                        .font(.headline)
                    Spacer()
                    Text(model.adbStatusMessage)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                switch selectedSection ?? .run {
                case .run:
                    GroupBox(t(model.language, "运行管理", "Run Manager")) {
                        RunHomeView()
                    }
                case .draw:
                    GroupBox(t(model.language, "抽卡", "Draw")) {
                        DrawHistoryView(runner: model.drawRunner)
                            .padding(8)
                    }
                case .record:
                    GroupBox(t(model.language, "录制", "Record")) {
                        RecorderView(
                            recorder: model.recorder,
                            lang: model.language,
                            deviceOptions: model.availableDevices,
                            refreshDevices: model.refreshADBAndDevices(adbInput:),
                            onDeviceChanged: model.rememberLastDevice(_:)
                        )
                    }
                case .editor:
                    GroupBox(t(model.language, "脚本编辑", "Script Editor")) {
                        ScriptEditorView(
                            model: model,
                            calibration: model.calibration,
                            debugRunner: model.runnerA,
                            showNewScriptDialog: $showNewScriptDialog,
                            newScriptName: $newScriptName,
                            onNavigateToRun: {
                                selectedSection = .run
                            }
                        )
                        .id("editor-\(model.selectedScriptName)")
                        .padding(8)
                    }
                case .settings:
                    SettingsView()
                }
            }
            .padding(12)
        }
        .background(
            ZStack {
                WindowAspectRatioGuard(ratio: CGSize(width: 1160, height: 780))
                WindowIdentifierGuard(identifier: mainWindowIdentifier)
                MainWindowOpenActionBinder()
            }
            .frame(width: 0, height: 0)
        )
        .onAppear {
            model.setup()
        }
        .alert(t(model.language, "录制完成", "Recording Completed"), isPresented: $model.showRunAfterRecordPrompt) {
            Button(t(model.language, "直接运行", "Run Now")) {
                let script = model.lastRecordedScriptToRun
                openWindow(id: "run-window", value: buildRunWindowValue(scriptName: script))
                model.lastRecordedScriptToRun = ""
            }
            Button(t(model.language, "稍后", "Later"), role: .cancel) {
                model.lastRecordedScriptToRun = ""
            }
        } message: {
            Text(t(model.language, "是否直接运行刚录制的脚本？", "Run the newly recorded script now?"))
        }
        .sheet(isPresented: $showNewScriptDialog) {
            VStack(alignment: .leading, spacing: 12) {
                Text(t(model.language, "新建脚本", "New Script"))
                    .font(.headline)
                TextField("example.json", text: $newScriptName)
                    .textFieldStyle(.roundedBorder)
                HStack {
                    Button(t(model.language, "取消", "Cancel")) {
                        showNewScriptDialog = false
                    }
                    Button(t(model.language, "创建", "Create")) {
                        model.newScript(name: newScriptName)
                        showNewScriptDialog = false
                    }
                    .keyboardShortcut(.defaultAction)
                }
            }
            .padding(20)
            .frame(width: 320)
        }
    }
}

@main
struct BSManagerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        Window("BSManager", id: mainWindowSceneID) {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 1160, minHeight: 780)
        }
        WindowGroup("Run", id: "run-window", for: String.self) { value in
            if let windowID = value.wrappedValue {
                let parsed = parseRunWindowValue(windowID)
                RunWindowView(windowID: parsed.windowID, initialScript: parsed.scriptName)
                    .environmentObject(model)
                    .frame(minWidth: 760, minHeight: 460)
            } else {
                Text(t(model.language, "无效运行窗口", "Invalid Run Window"))
                    .padding(20)
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !MainWindowCoordinator.shared.isMainWindowVisible {
            MainWindowCoordinator.shared.ensureMainWindowIsVisible()
        } else {
            NSApp.activate(ignoringOtherApps: true)
        }
        return true
    }
}
