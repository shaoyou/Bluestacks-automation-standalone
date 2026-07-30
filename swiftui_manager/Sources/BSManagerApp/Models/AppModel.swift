import SwiftUI
import Foundation

final class AppModel: ObservableObject {
    @Published var scripts: [ScriptFile] = []
    @Published var selectedScriptName: String = ""
    @Published var editorText: String = ""
    @Published var statusMessage: String = ""
    @Published var adbStatusMessage: String = "ADB 未检测"
    @Published var availableDevices: [String] = []
    @Published var showRunAfterRecordPrompt: Bool = false
    @Published var lastRecordedScriptToRun: String = ""
    @Published var language: AppLanguage = .zh
    @Published var editorWheelTurnSteps: [WheelTurnStep] = []
    @Published var editorSelectedDevice: String = ""

    @Published var drawRunner = RunnerModel(slotName: "DRAW")
    @Published var runnerA = RunnerModel(slotName: "A")
    @Published var runnerB = RunnerModel(slotName: "B")
    @Published var recorder: RecorderModel!
    @Published var calibration = CalibrationModel()
    @Published var diagnostic = RecordingDiagnosticModel()
    private let runnerALastScriptKey = "bs.runnerA.lastScript"
    private let runnerBLastScriptKey = "bs.runnerB.lastScript"
    private let lastDeviceKey = "bs.lastDevice"
    private let languageKey = "bs.ui.language"
    private let defaultDrawScriptFileName = "choukaka.json"

    private let defaultTemplate = """
{
  "device": "127.0.0.1:5555",
  "jitter_px": 3,
  "max_runtime_sec": 0,
  "variables": [
    { "name": "WAIT_SHORT", "value": "0.5", "note": "短等待秒数" }
  ],
  "actions": [
    { "type": "click", "x": 1000, "y": 650, "remark": "点击(1000,650)" },
    { "type": "wait", "seconds": 1.2, "remark": "等待1.2秒" },
    {
      "type": "loop",
      "count": -1,
      "actions": [
        {
          "type": "patrol",
          "remark": "往返巡逻1轮",
          "from": { "x": 300, "y": 400 },
          "to": { "x": 900, "y": 400 },
          "duration_ms": 600,
          "leg_wait_sec": 0.5,
          "rounds": 1
        },
        { "type": "wait", "seconds": 0.8, "jitter_seconds": 0.2, "remark": "等待0.8秒" }
      ]
    }
  ]
}
"""

    init() {
        let savedLang = UserDefaults.standard.string(forKey: languageKey) ?? "zh"
        language = AppLanguage(rawValue: savedLang) ?? .zh
        recorder = RecorderModel { [weak self] newFile in
            guard let self else { return }
            self.refreshScripts()
            if let newFile, self.scripts.contains(where: { $0.name == newFile }) {
                self.selectedScriptName = newFile
                self.loadSelectedScript()
                self.statusMessage = "录制完成: \(newFile)"
                self.lastRecordedScriptToRun = newFile
                self.showRunAfterRecordPrompt = true
            }
        }
    }

    func setup() {
        ensurePlansDir()
        refreshScripts()
        drawRunner.adbPath = recorder.adbPath
        calibration.adbPath = recorder.adbPath
        diagnostic.adbPath = recorder.adbPath
        refreshADBAndDevices(adbInput: recorder.adbPath)
    }

    func setLanguage(_ lang: AppLanguage) {
        language = lang
        UserDefaults.standard.set(lang.rawValue, forKey: languageKey)
    }

    func rememberLastDevice(_ device: String) {
        let trimmed = device.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        UserDefaults.standard.set(trimmed, forKey: lastDeviceKey)
    }

    private func preferredDevice(from devices: [String], current: String) -> String {
        guard !devices.isEmpty else { return current }
        let trimmedCurrent = current.trimmingCharacters(in: .whitespacesAndNewlines)
        if devices.contains(trimmedCurrent) {
            return trimmedCurrent
        }
        let recent = UserDefaults.standard.string(forKey: lastDeviceKey) ?? ""
        if devices.contains(recent) {
            return recent
        }
        return devices[0]
    }

    func preferredDeviceForCurrentList(current: String) -> String {
        preferredDevice(from: availableDevices, current: current)
    }

    func refreshADBAndDevices(adbInput: String) {
        let rawInput = adbInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "adb" : adbInput.trimmingCharacters(in: .whitespacesAndNewlines)
        DispatchQueue.global().async {
            guard let resolvedADB = resolveADBExecutable(rawInput) else {
                DispatchQueue.main.async {
                    self.availableDevices = []
                    self.adbStatusMessage = "ADB 异常: 未找到 \(rawInput)。请设置完整路径（如 /opt/homebrew/bin/adb）"
                }
                return
            }
            let refreshResult = listConnectedDevicesWithRecovery(adbPath: resolvedADB)
            let reachability = splitDevicesByReachability(adbPath: resolvedADB, devices: refreshResult.devices)
            let devices = reachability.healthy + reachability.unhealthy
            DispatchQueue.main.async {
                self.availableDevices = devices
                if devices.isEmpty {
                    if refreshResult.recovered {
                        self.adbStatusMessage = "ADB 已自动重启 (\(resolvedADB))，但当前无在线 device"
                    } else {
                        self.adbStatusMessage = "ADB 正常 (\(resolvedADB))，但当前无在线 device"
                    }
                } else {
                    let healthyCount = reachability.healthy.count
                    let unhealthyCount = reachability.unhealthy.count
                    if refreshResult.recovered {
                        self.adbStatusMessage = "ADB 已自动重启 (\(resolvedADB))，在线设备: \(devices.count)，可联通: \(healthyCount)，异常: \(unhealthyCount)"
                    } else {
                        self.adbStatusMessage = "ADB 正常 (\(resolvedADB))，在线设备: \(devices.count)，可联通: \(healthyCount)，异常: \(unhealthyCount)"
                    }
                    self.recorder.device = self.preferredDevice(from: devices, current: self.recorder.device)
                    self.drawRunner.device = self.preferredDevice(from: devices, current: self.drawRunner.device)
                    self.runnerA.device = self.preferredDevice(from: devices, current: self.runnerA.device)
                    self.runnerB.device = self.preferredDevice(from: devices, current: self.runnerB.device)
                    self.calibration.device = self.preferredDevice(from: devices, current: self.calibration.device)
                    self.diagnostic.device = self.preferredDevice(from: devices, current: self.diagnostic.device)
                    self.editorSelectedDevice = self.preferredDevice(from: devices, current: self.editorSelectedDevice)
                    self.rememberLastDevice(self.recorder.device)
                }
            }
        }
    }

    func forceRefreshADBAndDevices(adbInput: String) {
        let rawInput = adbInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "adb" : adbInput.trimmingCharacters(in: .whitespacesAndNewlines)
        DispatchQueue.global().async {
            guard let resolvedADB = resolveADBExecutable(rawInput) else {
                DispatchQueue.main.async {
                    self.availableDevices = []
                    self.adbStatusMessage = "ADB 异常: 未找到 \(rawInput)。请设置完整路径（如 /opt/homebrew/bin/adb）"
                }
                return
            }
            _ = runADBCommand(adbPath: resolvedADB, args: ["kill-server"])
            _ = runADBCommand(adbPath: resolvedADB, args: ["start-server"])
            let listed = listConnectedDevices(adbPath: resolvedADB)
            let reachability = splitDevicesByReachability(adbPath: resolvedADB, devices: listed)
            let devices = reachability.healthy + reachability.unhealthy
            DispatchQueue.main.async {
                self.availableDevices = devices
                if devices.isEmpty {
                    self.adbStatusMessage = "ADB 强制重启完成 (\(resolvedADB))，但当前无在线 device"
                } else {
                    self.adbStatusMessage = "ADB 强制重启完成 (\(resolvedADB))，在线设备: \(devices.count)，可联通: \(reachability.healthy.count)，异常: \(reachability.unhealthy.count)"
                    self.recorder.device = self.preferredDevice(from: devices, current: self.recorder.device)
                    self.drawRunner.device = self.preferredDevice(from: devices, current: self.drawRunner.device)
                    self.runnerA.device = self.preferredDevice(from: devices, current: self.runnerA.device)
                    self.runnerB.device = self.preferredDevice(from: devices, current: self.runnerB.device)
                    self.calibration.device = self.preferredDevice(from: devices, current: self.calibration.device)
                    self.diagnostic.device = self.preferredDevice(from: devices, current: self.diagnostic.device)
                    self.editorSelectedDevice = self.preferredDevice(from: devices, current: self.editorSelectedDevice)
                    self.rememberLastDevice(self.recorder.device)
                }
            }
        }
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
        if !FileManager.default.fileExists(atPath: imageTemplatesDir.path) {
            try? FileManager.default.createDirectory(at: imageTemplatesDir, withIntermediateDirectories: true)
        }
        if !FileManager.default.fileExists(atPath: recordingProfilesDir.path) {
            try? FileManager.default.createDirectory(at: recordingProfilesDir, withIntermediateDirectories: true)
        }
        if !FileManager.default.fileExists(atPath: diagnosticsDir.path) {
            try? FileManager.default.createDirectory(at: diagnosticsDir, withIntermediateDirectories: true)
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
            drawRunner.selectedScript = ""
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
        drawRunner.selectedScript = scripts.contains(where: { $0.name == defaultDrawScriptFileName })
            ? defaultDrawScriptFileName
            : selectedScriptName
        rememberRunnerScript(slot: "A", name: runnerA.selectedScript)
        rememberRunnerScript(slot: "B", name: runnerB.selectedScript)
    }

    func scriptURL(named: String) -> URL? {
        scripts.first(where: { $0.name == named })?.url
    }

    func drawScriptName() -> String {
        scripts.contains(where: { $0.name == defaultDrawScriptFileName }) ? defaultDrawScriptFileName : selectedScriptName
    }

    func syncDrawRunnerDefaults() {
        drawRunner.adbPath = recorder.adbPath
        let scriptName = drawScriptName()
        if !scriptName.isEmpty {
            drawRunner.selectedScript = scriptName
        }
        drawRunner.device = preferredDevice(from: availableDevices, current: drawRunner.device)
    }

    func startDrawRun() {
        syncDrawRunnerDefaults()
        let scriptName = drawScriptName()
        guard !scriptName.isEmpty else {
            statusMessage = "抽卡失败: 未找到默认脚本 choukaka.json"
            return
        }
        guard let scriptURL = scriptURL(named: scriptName) else {
            statusMessage = "抽卡失败: 脚本不存在 \(scriptName)"
            return
        }
        let device = preferredDevice(from: availableDevices, current: drawRunner.device)
        drawRunner.device = device
        if device.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            statusMessage = "抽卡失败: 没有可连接设备"
            return
        }
        rememberLastDevice(device)
        drawRunner.start(scriptURL: scriptURL)
    }

    func preferredEditorDebugDevice() -> String {
        let candidates = [
            editorSelectedDevice,
            runnerA.device,
            recorder.device,
            calibration.device,
            runnerB.device,
        ]
        for item in candidates {
            let preferred = preferredDevice(from: availableDevices, current: item)
            let trimmed = preferred.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                return trimmed
            }
        }
        return preferredDevice(from: availableDevices, current: "")
    }

    func startEditorDebugRun(completion: @escaping (Bool) -> Void) {
        let scriptName = selectedScriptName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !scriptName.isEmpty, let scriptURL = scriptURL(named: scriptName) else {
            DispatchQueue.main.async {
                self.statusMessage = "调试失败: 当前没有可运行脚本"
                completion(false)
            }
            return
        }
        let adbInput = recorder.adbPath
        let device = preferredEditorDebugDevice()
        DispatchQueue.global().async {
            guard let resolvedADB = resolveADBExecutable(adbInput) else {
                DispatchQueue.main.async {
                    self.statusMessage = "调试失败: 未找到 adb"
                    completion(false)
                }
                return
            }
            guard !device.isEmpty, isADBDeviceReachable(adbPath: resolvedADB, device: device) else {
                DispatchQueue.main.async {
                    self.statusMessage = "调试失败: 没有可连接设备"
                    completion(false)
                }
                return
            }
            DispatchQueue.main.async {
                self.runnerA.adbPath = adbInput
                self.runnerA.device = device
                self.runnerA.selectedScript = scriptName
                self.editorSelectedDevice = device
                self.rememberLastDevice(device)
                self.rememberRunnerScript(slot: "A", name: scriptName)
                self.statusMessage = "调试运行: \(scriptName)"
                self.runnerA.start(scriptURL: scriptURL)
                completion(true)
            }
        }
    }

    func loadSelectedScript() {
        guard let url = scriptURL(named: selectedScriptName) else { return }
        editorText = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
    }

    func selectScript(named name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, scripts.contains(where: { $0.name == trimmed }) else { return }
        if selectedScriptName != trimmed {
            selectedScriptName = trimmed
        }
        loadSelectedScript()
        statusMessage = "已切换脚本: \(trimmed)"
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
            statusMessage = "已保存: \(url.path)"
            refreshScripts()
        } catch {
            statusMessage = "保存失败: \(error.localizedDescription)"
        }
    }
}
