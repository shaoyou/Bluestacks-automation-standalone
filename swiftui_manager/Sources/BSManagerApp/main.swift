import SwiftUI
import Foundation
import AppKit

private let appRoot = URL(fileURLWithPath: "/Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone", isDirectory: true)
private let plansDir = appRoot.appendingPathComponent("plans", isDirectory: true)
private let botScript = appRoot.appendingPathComponent("adb_bot.py")
private let recorderScript = appRoot.appendingPathComponent("record_touch.py")

struct ScriptFile: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let url: URL
}

final class RunnerModel: ObservableObject {
    let slotName: String
    @Published var selectedScript: String = ""
    @Published var device: String = ""
    @Published var adbPath: String = "adb"
    @Published var logs: String = ""
    @Published var isRunning: Bool = false

    private var process: Process?
    private var readHandle: FileHandle?
    private var exitObserver: NSObjectProtocol?

    init(slotName: String) {
        self.slotName = slotName
    }

    func appendLog(_ line: String) {
        DispatchQueue.main.async {
            self.logs += line
        }
    }

    func start(scriptURL: URL?) {
        guard !isRunning else {
            appendLog("[\(slotName)] already running\n")
            return
        }
        guard let scriptURL else {
            appendLog("[\(slotName)] no script selected\n")
            return
        }
        guard FileManager.default.fileExists(atPath: scriptURL.path) else {
            appendLog("[\(slotName)] script not found: \(scriptURL.path)\n")
            return
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        var args = [botScript.path, "--plan", scriptURL.path, "--adb", adbPath.isEmpty ? "adb" : adbPath]
        if !device.trimmingCharacters(in: .whitespaces).isEmpty {
            args += ["--device", device.trimmingCharacters(in: .whitespaces)]
        }
        proc.arguments = args

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe

        readHandle = pipe.fileHandleForReading
        readHandle?.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { return }
            if let text = String(data: data, encoding: .utf8), let self {
                self.appendLog("[\(self.slotName)] \(text)")
            }
        }

        appendLog("[\(slotName)] start: /usr/bin/python3 \(args.joined(separator: " "))\n")
        do {
            try proc.run()
            process = proc
            isRunning = true
            exitObserver = NotificationCenter.default.addObserver(
                forName: Process.didTerminateNotification,
                object: proc,
                queue: .main
            ) { [weak self] _ in
                guard let self else { return }
                self.readHandle?.readabilityHandler = nil
                self.readHandle = nil
                let code = proc.terminationStatus
                self.appendLog("[\(self.slotName)] exit code: \(code)\n")
                self.isRunning = false
                self.process = nil
            }
        } catch {
            appendLog("[\(slotName)] failed to start: \(error)\n")
            readHandle?.readabilityHandler = nil
            readHandle = nil
            process = nil
            isRunning = false
        }
    }

    func stop() {
        guard let proc = process else {
            appendLog("[\(slotName)] not running\n")
            return
        }
        appendLog("[\(slotName)] stopping...\n")
        proc.interrupt()
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) { [weak self] in
            guard let self, let running = self.process, running.isRunning else { return }
            running.terminate()
        }
    }

    deinit {
        readHandle?.readabilityHandler = nil
        if let observer = exitObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }
}

final class RecorderModel: ObservableObject {
    @Published var outputName: String = "recorded.json"
    @Published var device: String = ""
    @Published var adbPath: String = "adb"
    @Published var loopCount: String = "-1"
    @Published var cleanNoise: Bool = true
    @Published var invertX: Bool = false
    @Published var invertY: Bool = false
    @Published var swapXY: Bool = false
    @Published var mappingLocked: Bool = true
    @Published var logs: String = ""
    @Published var isRecording: Bool = false

    private var process: Process?
    private var readHandle: FileHandle?
    private var exitObserver: NSObjectProtocol?
    private let onFinished: (String?) -> Void

    init(onFinished: @escaping (String?) -> Void) {
        self.onFinished = onFinished
    }

    private func normalizedOutputName() -> String {
        var name = outputName.trimmingCharacters(in: .whitespacesAndNewlines)
        if name.isEmpty {
            name = "recorded.json"
        }
        if !name.hasSuffix(".json") {
            name += ".json"
        }
        return name
    }

    func appendLog(_ line: String) {
        DispatchQueue.main.async {
            self.logs += line
        }
    }

    func start() {
        guard !isRecording else {
            appendLog("[REC] already recording\n")
            return
        }

        let fileName = normalizedOutputName()
        let outputURL = plansDir.appendingPathComponent(fileName)

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        let loop = Int(loopCount.trimmingCharacters(in: .whitespacesAndNewlines)) ?? -1
        var args = [
            recorderScript.path,
            "--output", outputURL.path,
            "--adb", adbPath.isEmpty ? "adb" : adbPath,
            "--loop-count", String(loop),
        ]
        if mappingLocked {
            args += ["--mapping-lock"]
        }
        if !cleanNoise {
            args += ["--no-clean-noise"]
        }
        if invertX {
            args += ["--invert-x"]
        }
        if invertY {
            args += ["--invert-y"]
        }
        if swapXY {
            args += ["--swap-xy"]
        }
        if !device.trimmingCharacters(in: .whitespaces).isEmpty {
            args += ["--device", device.trimmingCharacters(in: .whitespaces)]
        }
        proc.arguments = args

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe

        readHandle = pipe.fileHandleForReading
        readHandle?.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { return }
            if let text = String(data: data, encoding: .utf8), let self {
                self.appendLog("[REC] \(text)")
            }
        }

        appendLog("[REC] start: /usr/bin/python3 \(args.joined(separator: " "))\n")
        do {
            try proc.run()
            process = proc
            isRecording = true
            exitObserver = NotificationCenter.default.addObserver(
                forName: Process.didTerminateNotification,
                object: proc,
                queue: .main
            ) { [weak self] _ in
                guard let self else { return }
                self.readHandle?.readabilityHandler = nil
                self.readHandle = nil
                let code = proc.terminationStatus
                self.appendLog("[REC] exit code: \(code)\n")
                self.isRecording = false
                self.process = nil
                self.onFinished(code == 0 ? fileName : nil)
            }
        } catch {
            appendLog("[REC] failed to start: \(error)\n")
            readHandle?.readabilityHandler = nil
            readHandle = nil
            process = nil
            isRecording = false
        }
    }

    func stop() {
        guard let proc = process else {
            appendLog("[REC] not recording\n")
            return
        }
        appendLog("[REC] stopping...\n")
        proc.interrupt()
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) { [weak self] in
            guard let self, let running = self.process, running.isRunning else { return }
            running.terminate()
        }
    }

    deinit {
        readHandle?.readabilityHandler = nil
        if let observer = exitObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }
}

final class CalibrationModel: ObservableObject {
    @Published var logs: String = ""
    private var isBusy = false

    func appendLog(_ line: String) {
        DispatchQueue.main.async {
            self.logs += line
        }
    }

    private func runADB(adbPath: String, device: String, shellArgs: [String], title: String) {
        if isBusy {
            appendLog("[CAL] busy, wait current task\n")
            return
        }
        isBusy = true
        appendLog("[CAL] \(title)\n")
        DispatchQueue.global().async {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: adbPath.isEmpty ? "/usr/bin/env" : "/usr/bin/env")
            var args = [adbPath.isEmpty ? "adb" : adbPath]
            let dev = device.trimmingCharacters(in: .whitespacesAndNewlines)
            if !dev.isEmpty {
                args += ["-s", dev]
            }
            args += ["shell"] + shellArgs
            proc.arguments = args
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = pipe
            do {
                try proc.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                let text = String(data: data, encoding: .utf8) ?? ""
                let code = proc.waitUntilExitOrReturn()
                self.appendLog("[CAL] cmd: \(args.joined(separator: " "))\n")
                if !text.isEmpty {
                    self.appendLog("[CAL] \(text)\n")
                }
                self.appendLog("[CAL] exit: \(code)\n")
            } catch {
                self.appendLog("[CAL] failed: \(error)\n")
            }
            DispatchQueue.main.async {
                self.isBusy = false
            }
        }
    }

    func ping(adbPath: String, device: String) {
        runADB(adbPath: adbPath, device: device, shellArgs: ["getprop", "ro.build.version.release"], title: "Ping")
    }

    func screenSize(adbPath: String, device: String) {
        runADB(adbPath: adbPath, device: device, shellArgs: ["wm", "size"], title: "Screen Size")
    }

    func tapTest(adbPath: String, device: String) {
        runADB(adbPath: adbPath, device: device, shellArgs: ["input", "tap", "540", "960"], title: "Tap Test (540,960)")
    }

    func swipeToBottomLeft(adbPath: String, device: String) {
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "swipe", "540", "960", "80", "1840", "500"],
            title: "Swipe to Bottom-Left Test"
        )
    }

    func swipeToTopRight(adbPath: String, device: String) {
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "swipe", "540", "960", "1000", "80", "500"],
            title: "Swipe to Top-Right Test"
        )
    }

    func swipeDownTest(adbPath: String, device: String) {
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "swipe", "540", "500", "540", "1400", "500"],
            title: "Swipe Down Test"
        )
    }

    func swipeUpTest(adbPath: String, device: String) {
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "swipe", "540", "1400", "540", "500", "500"],
            title: "Swipe Up Test"
        )
    }
}

private extension Process {
    func waitUntilExitOrReturn() -> Int32 {
        self.waitUntilExit()
        return self.terminationStatus
    }
}

final class AppModel: ObservableObject {
    @Published var scripts: [ScriptFile] = []
    @Published var selectedScriptName: String = ""
    @Published var editorText: String = ""
    @Published var statusMessage: String = ""

    @Published var runnerA = RunnerModel(slotName: "A")
    @Published var runnerB = RunnerModel(slotName: "B")
    @Published var recorder: RecorderModel!
    @Published var calibration = CalibrationModel()
    private let runnerALastScriptKey = "bs.runnerA.lastScript"
    private let runnerBLastScriptKey = "bs.runnerB.lastScript"

    private let defaultTemplate = """
{
  "device": "127.0.0.1:5555",
  "jitter_px": 3,
  "max_runtime_sec": 0,
  "actions": [
    { "type": "click", "x": 1000, "y": 650 },
    { "type": "wait", "seconds": 1.2 },
    {
      "type": "loop",
      "count": -1,
      "actions": [
        {
          "type": "patrol",
          "from": { "x": 300, "y": 400 },
          "to": { "x": 900, "y": 400 },
          "duration_ms": 600,
          "leg_wait_sec": 0.5,
          "rounds": 1
        },
        { "type": "wait", "seconds": 0.8, "jitter_seconds": 0.2 }
      ]
    }
  ]
}
"""

    init() {
        recorder = RecorderModel { [weak self] newFile in
            guard let self else { return }
            self.refreshScripts()
            if let newFile, self.scripts.contains(where: { $0.name == newFile }) {
                self.selectedScriptName = newFile
                self.loadSelectedScript()
                self.statusMessage = "录制完成: \(newFile)"
            }
        }
    }

    func setup() {
        ensurePlansDir()
        refreshScripts()
    }

    func rememberRunnerScript(slot: String, name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if slot == "A" {
            UserDefaults.standard.set(trimmed, forKey: runnerALastScriptKey)
        } else if slot == "B" {
            UserDefaults.standard.set(trimmed, forKey: runnerBLastScriptKey)
        }
    }

    func ensurePlansDir() {
        if !FileManager.default.fileExists(atPath: plansDir.path) {
            try? FileManager.default.createDirectory(at: plansDir, withIntermediateDirectories: true)
        }
    }

    func refreshScripts() {
        let files = (try? FileManager.default.contentsOfDirectory(at: plansDir, includingPropertiesForKeys: nil)) ?? []
        scripts = files
            .filter { $0.pathExtension.lowercased() == "json" }
            .map { ScriptFile(name: $0.lastPathComponent, url: $0) }
            .sorted { $0.name < $1.name }

        if scripts.isEmpty {
            selectedScriptName = ""
            editorText = ""
            runnerA.selectedScript = ""
            runnerB.selectedScript = ""
            return
        }

        if !scripts.contains(where: { $0.name == selectedScriptName }) {
            selectedScriptName = scripts[0].name
        }
        loadSelectedScript()

        let savedA = UserDefaults.standard.string(forKey: runnerALastScriptKey) ?? ""
        let savedB = UserDefaults.standard.string(forKey: runnerBLastScriptKey) ?? ""

        if runnerA.selectedScript.isEmpty || !scripts.contains(where: { $0.name == runnerA.selectedScript }) {
            runnerA.selectedScript = scripts.contains(where: { $0.name == savedA }) ? savedA : selectedScriptName
        }
        if runnerB.selectedScript.isEmpty || !scripts.contains(where: { $0.name == runnerB.selectedScript }) {
            runnerB.selectedScript = scripts.contains(where: { $0.name == savedB }) ? savedB : selectedScriptName
        }
        rememberRunnerScript(slot: "A", name: runnerA.selectedScript)
        rememberRunnerScript(slot: "B", name: runnerB.selectedScript)
    }

    func scriptURL(named: String) -> URL? {
        scripts.first(where: { $0.name == named })?.url
    }

    func loadSelectedScript() {
        guard let url = scriptURL(named: selectedScriptName) else { return }
        editorText = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
    }

    func newScript(name: String) {
        var fileName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !fileName.isEmpty else { return }
        if !fileName.hasSuffix(".json") {
            fileName += ".json"
        }
        let url = plansDir.appendingPathComponent(fileName)
        if FileManager.default.fileExists(atPath: url.path) {
            statusMessage = "文件已存在: \(fileName)"
            return
        }
        do {
            try defaultTemplate.write(to: url, atomically: true, encoding: .utf8)
            statusMessage = "已创建: \(fileName)"
            refreshScripts()
            selectedScriptName = fileName
            loadSelectedScript()
        } catch {
            statusMessage = "创建失败: \(error.localizedDescription)"
        }
    }

    func saveCurrentScript() {
        guard let url = scriptURL(named: selectedScriptName) else {
            statusMessage = "未选中脚本"
            return
        }
        guard let data = editorText.data(using: .utf8) else {
            statusMessage = "内容编码失败"
            return
        }
        do {
            let obj = try JSONSerialization.jsonObject(with: data)
            let pretty = try JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])
            guard let text = String(data: pretty, encoding: .utf8) else {
                statusMessage = "格式化 JSON 失败"
                return
            }
            try text.write(to: url, atomically: true, encoding: .utf8)
            editorText = text
            statusMessage = "已保存: \(selectedScriptName)"
            refreshScripts()
        } catch {
            statusMessage = "保存失败: \(error.localizedDescription)"
        }
    }
}

struct RunnerView: View {
    @ObservedObject var runner: RunnerModel
    let scripts: [String]
    let resolveScriptURL: (String) -> URL?
    let onSelectionChanged: (String, String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Script")
                Picker("Script", selection: $runner.selectedScript) {
                    ForEach(scripts, id: \.self) { name in
                        Text(name).tag(name)
                    }
                }
                .frame(width: 240)
                .onChange(of: runner.selectedScript) { newValue in
                    onSelectionChanged(runner.slotName, newValue)
                }
            }
            HStack {
                Text("Device")
                TextField("127.0.0.1:5555", text: $runner.device)
                    .textFieldStyle(.roundedBorder)
            }
            HStack {
                Text("ADB")
                TextField("adb", text: $runner.adbPath)
                    .textFieldStyle(.roundedBorder)
            }
            HStack {
                Button("Start \(runner.slotName)") {
                    runner.start(scriptURL: resolveScriptURL(runner.selectedScript))
                }
                .disabled(runner.isRunning)
                Button("Stop \(runner.slotName)") {
                    runner.stop()
                }
            }
            ScrollView {
                Text(runner.logs.isEmpty ? "No logs" : runner.logs)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(6)
                    .textSelection(.enabled)
            }
            .frame(height: 180)
            .background(Color(nsColor: .textBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.gray.opacity(0.4), lineWidth: 1)
            )
        }
        .padding(10)
    }
}

struct RecorderView: View {
    @ObservedObject var recorder: RecorderModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Output")
                TextField("recorded.json", text: $recorder.outputName)
                    .textFieldStyle(.roundedBorder)
            }
            HStack {
                Text("Device")
                TextField("127.0.0.1:5555", text: $recorder.device)
                    .textFieldStyle(.roundedBorder)
            }
            HStack {
                Text("ADB")
                TextField("adb", text: $recorder.adbPath)
                    .textFieldStyle(.roundedBorder)
                Text("Loop")
                TextField("-1", text: $recorder.loopCount)
                    .frame(width: 64)
                    .textFieldStyle(.roundedBorder)
            }
            Toggle("Clean Noise (Mild)", isOn: $recorder.cleanNoise)
            Toggle("Lock Mapping", isOn: $recorder.mappingLocked)
            HStack {
                Toggle("Invert X", isOn: $recorder.invertX)
                Toggle("Invert Y", isOn: $recorder.invertY)
                Toggle("Swap XY", isOn: $recorder.swapXY)
            }
            .disabled(recorder.mappingLocked)
            HStack {
                Button("Start Recording") {
                    recorder.start()
                }
                .disabled(recorder.isRecording)
                Button("Stop Recording") {
                    recorder.stop()
                }
            }
            ScrollView {
                Text(recorder.logs.isEmpty ? "No logs" : recorder.logs)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(6)
                    .textSelection(.enabled)
            }
            .frame(height: 140)
            .background(Color(nsColor: .textBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.gray.opacity(0.4), lineWidth: 1)
            )
        }
        .padding(10)
    }
}

struct CalibrationView: View {
    @ObservedObject var recorder: RecorderModel
    @ObservedObject var calibration: CalibrationModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Use same Device/ADB as Recorder")
                .font(.footnote)
                .foregroundStyle(.secondary)
            HStack {
                Button("Ping") {
                    calibration.ping(adbPath: recorder.adbPath, device: recorder.device)
                }
                Button("Screen Size") {
                    calibration.screenSize(adbPath: recorder.adbPath, device: recorder.device)
                }
                Button("Tap Test") {
                    calibration.tapTest(adbPath: recorder.adbPath, device: recorder.device)
                }
            }
            HStack {
                Button("Swipe to Bottom-Left") {
                    calibration.swipeToBottomLeft(adbPath: recorder.adbPath, device: recorder.device)
                }
                Button("Swipe to Top-Right") {
                    calibration.swipeToTopRight(adbPath: recorder.adbPath, device: recorder.device)
                }
            }
            HStack {
                Button("Swipe Down Test") {
                    calibration.swipeDownTest(adbPath: recorder.adbPath, device: recorder.device)
                }
                Button("Swipe Up Test") {
                    calibration.swipeUpTest(adbPath: recorder.adbPath, device: recorder.device)
                }
            }
            ScrollView {
                Text(calibration.logs.isEmpty ? "No logs" : calibration.logs)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(6)
                    .textSelection(.enabled)
            }
            .frame(height: 140)
            .background(Color(nsColor: .textBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.gray.opacity(0.4), lineWidth: 1)
            )
        }
        .padding(10)
    }
}

struct ContentView: View {
    @StateObject private var model = AppModel()
    @State private var showNewScriptDialog = false
    @State private var newScriptName = ""

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                GroupBox("Calibration") {
                    CalibrationView(recorder: model.recorder, calibration: model.calibration)
                }
                HStack {
                    Button("Refresh") {
                        model.refreshScripts()
                    }
                    Button("New") {
                        newScriptName = ""
                        showNewScriptDialog = true
                    }
                    Button("Save") {
                        model.saveCurrentScript()
                    }
                }

                Picker("Script", selection: $model.selectedScriptName) {
                    ForEach(model.scripts.map(\.name), id: \.self) { name in
                        Text(name).tag(name)
                    }
                }
                .onChange(of: model.selectedScriptName) { _ in
                    model.loadSelectedScript()
                }

                TextEditor(text: $model.editorText)
                    .font(.system(size: 12, design: .monospaced))
                    .border(Color.gray.opacity(0.4), width: 1)

                Text(model.statusMessage)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .frame(minWidth: 480)

            ScrollView {
                VStack(spacing: 12) {
                    GroupBox("Recorder") {
                        RecorderView(recorder: model.recorder)
                    }
                    GroupBox("Runner A") {
                        RunnerView(
                            runner: model.runnerA,
                            scripts: model.scripts.map(\.name),
                            resolveScriptURL: model.scriptURL(named:),
                            onSelectionChanged: model.rememberRunnerScript(slot:name:)
                        )
                    }
                    GroupBox("Runner B") {
                        RunnerView(
                            runner: model.runnerB,
                            scripts: model.scripts.map(\.name),
                            resolveScriptURL: model.scriptURL(named:),
                            onSelectionChanged: model.rememberRunnerScript(slot:name:)
                        )
                    }
                }
            }
            .frame(minWidth: 620)
        }
        .padding(12)
        .onAppear {
            model.setup()
        }
        .sheet(isPresented: $showNewScriptDialog) {
            VStack(alignment: .leading, spacing: 12) {
                Text("New Script")
                    .font(.headline)
                TextField("example.json", text: $newScriptName)
                    .textFieldStyle(.roundedBorder)
                HStack {
                    Button("Cancel") {
                        showNewScriptDialog = false
                    }
                    Button("Create") {
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

    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 1160, minHeight: 780)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}
