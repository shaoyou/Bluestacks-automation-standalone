import SwiftUI
import Foundation
import AppKit

private let appRoot = URL(fileURLWithPath: "/Users/admins/Documents/Playground/Bluestacks-automation-standalone", isDirectory: true)
private let plansDir = appRoot.appendingPathComponent("plans", isDirectory: true)
private let botScript = appRoot.appendingPathComponent("adb_bot.py")
private let recorderScript = appRoot.appendingPathComponent("record_touch.py")
private let appVersion = "1.1.0"

private func mergedEnvironment() -> [String: String] {
    var env = ProcessInfo.processInfo.environment
    let sdkADB = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Android/sdk/platform-tools")
    let extra = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin", sdkADB]
    let current = env["PATH"] ?? ""
    env["PATH"] = ([current] + extra)
        .flatMap { $0.split(separator: ":").map(String.init) }
        .filter { !$0.isEmpty }
        .reduce(into: [String]()) { acc, item in
            if !acc.contains(item) {
                acc.append(item)
            }
        }
        .joined(separator: ":")
    return env
}

private func expandTilde(_ path: String) -> String {
    (path as NSString).expandingTildeInPath
}

private func isExecutable(_ path: String) -> Bool {
    FileManager.default.isExecutableFile(atPath: path)
}

private func resolveADBExecutable(_ rawInput: String) -> String? {
    let input = rawInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "adb" : rawInput.trimmingCharacters(in: .whitespacesAndNewlines)
    if input.contains("/") || input.hasPrefix("~") {
        let expanded = expandTilde(input)
        return isExecutable(expanded) ? expanded : nil
    }

    let whichProc = Process()
    let pipe = Pipe()
    whichProc.executableURL = URL(fileURLWithPath: "/usr/bin/which")
    whichProc.arguments = [input]
    whichProc.environment = mergedEnvironment()
    whichProc.standardOutput = pipe
    whichProc.standardError = Pipe()
    do {
        try whichProc.run()
        whichProc.waitUntilExit()
        if whichProc.terminationStatus == 0 {
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            if let out = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines),
               !out.isEmpty,
               isExecutable(out) {
                return out
            }
        }
    } catch {
        return nil
    }

    if input == "adb" {
        let sdkADB = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Android/sdk/platform-tools/adb")
        let candidates = ["/opt/homebrew/bin/adb", "/usr/local/bin/adb", sdkADB]
        for item in candidates where isExecutable(item) {
            return item
        }
    }
    return nil
}

private func runADBCommand(adbPath: String, args: [String]) -> (code: Int32, text: String) {
    let proc = Process()
    let pipe = Pipe()
    proc.executableURL = URL(fileURLWithPath: adbPath)
    proc.arguments = args
    proc.environment = mergedEnvironment()
    proc.standardOutput = pipe
    proc.standardError = pipe
    do {
        try proc.run()
        proc.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let text = String(data: data, encoding: .utf8) ?? ""
        return (proc.terminationStatus, text)
    } catch {
        return (-1, String(describing: error))
    }
}

private func parseDeviceList(_ text: String) -> [String] {
    text
        .split(separator: "\n")
        .map(String.init)
        .filter { $0.contains("\tdevice") }
        .compactMap { $0.split(separator: "\t").first.map(String.init) }
}

private func listConnectedDevices(adbPath: String) -> [String] {
    let first = runADBCommand(adbPath: adbPath, args: ["devices"])
    return parseDeviceList(first.text)
}

private func listConnectedDevicesWithRecovery(adbPath: String) -> (devices: [String], recovered: Bool) {
    let first = runADBCommand(adbPath: adbPath, args: ["devices"])
    let firstDevices = parseDeviceList(first.text)
    if !firstDevices.isEmpty {
        return (firstDevices, false)
    }

    _ = runADBCommand(adbPath: adbPath, args: ["start-server"])
    let second = runADBCommand(adbPath: adbPath, args: ["devices"])
    let secondDevices = parseDeviceList(second.text)
    if !secondDevices.isEmpty {
        return (secondDevices, true)
    }

    _ = runADBCommand(adbPath: adbPath, args: ["kill-server"])
    _ = runADBCommand(adbPath: adbPath, args: ["start-server"])
    let third = runADBCommand(adbPath: adbPath, args: ["devices"])
    let thirdDevices = parseDeviceList(third.text)
    return (thirdDevices, true)
}

private func readScreenSize(adbPath: String, device: String) -> (Int, Int)? {
    let proc = Process()
    let pipe = Pipe()
    proc.executableURL = URL(fileURLWithPath: adbPath)
    var args: [String] = []
    let dev = device.trimmingCharacters(in: .whitespacesAndNewlines)
    if !dev.isEmpty {
        args += ["-s", dev]
    }
    args += ["shell", "wm", "size"]
    proc.arguments = args
    proc.environment = mergedEnvironment()
    proc.standardOutput = pipe
    proc.standardError = pipe
    do {
        try proc.run()
        proc.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let text = String(data: data, encoding: .utf8) ?? ""
        let regex = try NSRegularExpression(pattern: "(\\d+)x(\\d+)")
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = regex.firstMatch(in: text, options: [], range: range),
              let wRange = Range(match.range(at: 1), in: text),
              let hRange = Range(match.range(at: 2), in: text),
              let w = Int(text[wRange]),
              let h = Int(text[hRange]) else {
            return nil
        }
        return (w, h)
    } catch {
        return nil
    }
}

private func openScriptsDirectoryInFinder() {
    NSWorkspace.shared.open(plansDir)
}

private let runWindowScriptSeparator = ":::script:::"

private func buildRunWindowValue(scriptName: String? = nil) -> String {
    let id = UUID().uuidString
    guard let scriptName, !scriptName.isEmpty else {
        return id
    }
    return "\(id)\(runWindowScriptSeparator)\(scriptName)"
}

private func parseRunWindowValue(_ value: String) -> (windowID: String, scriptName: String?) {
    let parts = value.components(separatedBy: runWindowScriptSeparator)
    if parts.count >= 2 {
        return (parts[0], parts[1])
    }
    return (value, nil)
}

enum AppLanguage: String, CaseIterable, Identifiable {
    case zh = "zh"
    case en = "en"
    var id: String { rawValue }
    var displayName: String { self == .zh ? "中文" : "English" }
}

private func t(_ lang: AppLanguage, _ zh: String, _ en: String) -> String {
    lang == .zh ? zh : en
}

struct ScriptFile: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let url: URL
}

final class RunnerModel: ObservableObject {
    private static let maxLogChars = 80_000
    private static let logFlushIntervalSec: Double = 0.12
    let slotName: String
    @Published var selectedScript: String = ""
    @Published var device: String = ""
    @Published var adbPath: String = "adb"
    @Published var logs: String = ""
    @Published var isRunning: Bool = false
    @Published var cycleDurationSec: Double = 0
    @Published var cycleProgressSec: Double = 0
    @Published var showRealtimeCommandLogs: Bool = false

    private var process: Process?
    private var readHandle: FileHandle?
    private var exitObserver: NSObjectProtocol?
    private var progressTimer: DispatchSourceTimer?
    private var progressStartAt: Date?
    private let logQueue = DispatchQueue(label: "bs.runner.log.queue")
    private var pendingLogs: [String] = []
    private var logFlushScheduled = false

    init(slotName: String) {
        self.slotName = slotName
    }

    func appendLog(_ line: String) {
        logQueue.async {
            self.pendingLogs.append(line)
            guard !self.logFlushScheduled else { return }
            self.logFlushScheduled = true
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.logFlushIntervalSec) { [weak self] in
                self?.flushPendingLogs()
            }
        }
    }

    private func flushPendingLogs() {
        let batch: [String] = logQueue.sync {
            let data = pendingLogs
            pendingLogs.removeAll(keepingCapacity: true)
            logFlushScheduled = false
            return data
        }
        guard !batch.isEmpty else { return }
        let mergedBatch = batch.reversed().joined()
        let merged = mergedBatch + logs
        if merged.count > Self.maxLogChars {
            let marker = "[\(slotName)] ...old logs trimmed...\n"
            let budget = max(0, Self.maxLogChars - marker.count)
            logs = String(merged.prefix(budget)) + marker
        } else {
            logs = merged
        }
    }

    func clearLogs() {
        logQueue.async {
            self.pendingLogs.removeAll(keepingCapacity: false)
            self.logFlushScheduled = false
        }
        DispatchQueue.main.async {
            self.logs = ""
        }
    }

    var progressText: String {
        guard cycleDurationSec > 0 else { return "--" }
        return String(format: "%.1f / %.1fs", cycleProgressSec, cycleDurationSec)
    }

    private func estimateActionsDuration(_ actions: [Any]) -> Double {
        var total: Double = 0
        for item in actions {
            guard let action = item as? [String: Any], let type = action["type"] as? String else { continue }
            switch type {
            case "wait":
                total += Double(action["seconds"] as? Double ?? 0)
            case "swipe":
                total += Double(action["duration_ms"] as? Int ?? 0) / 1000.0
            case "trace":
                if let points = action["points"] as? [[String: Any]],
                   let first = points.first,
                   let last = points.last,
                   let t0 = first["t_ms"] as? Int,
                   let t1 = last["t_ms"] as? Int {
                    total += max(0, Double(t1 - t0) / 1000.0)
                }
            case "sequence":
                if let nested = action["actions"] as? [Any] {
                    total += estimateActionsDuration(nested)
                }
            case "loop":
                if let nested = action["actions"] as? [Any] {
                    let cycle = estimateActionsDuration(nested)
                    let count = action["count"] as? Int ?? 1
                    total += count <= 0 ? cycle : cycle * Double(count)
                }
            case "patrol":
                let durationMs = action["duration_ms"] as? Int ?? 500
                let legWait = action["leg_wait_sec"] as? Double ?? 0.4
                let rounds = action["rounds"] as? Int ?? 1
                let r = rounds <= 0 ? 1 : rounds
                total += Double(r) * (Double(durationMs) / 1000.0 * 2.0 + legWait * 2.0)
            default:
                continue
            }
        }
        return total
    }

    private func estimateCycleDuration(scriptURL: URL) -> Double {
        guard let data = try? Data(contentsOf: scriptURL),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let actions = obj["actions"] as? [Any] else {
            return 0
        }
        let duration = estimateActionsDuration(actions)
        return duration > 0 ? duration : 0
    }

    private func stopProgressTimer() {
        progressTimer?.cancel()
        progressTimer = nil
        progressStartAt = nil
        DispatchQueue.main.async {
            self.cycleProgressSec = 0
        }
    }

    private func startProgressTimer(scriptURL: URL) {
        stopProgressTimer()
        let duration = estimateCycleDuration(scriptURL: scriptURL)
        DispatchQueue.main.async {
            self.cycleDurationSec = duration
            self.cycleProgressSec = 0
        }
        guard duration > 0 else { return }
        progressStartAt = Date()
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global())
        timer.schedule(deadline: .now(), repeating: .milliseconds(200))
        timer.setEventHandler { [weak self] in
            guard let self, let start = self.progressStartAt else { return }
            let elapsed = Date().timeIntervalSince(start)
            let cycle = duration > 0 ? elapsed.truncatingRemainder(dividingBy: duration) : 0
            DispatchQueue.main.async {
                self.cycleProgressSec = cycle
            }
        }
        progressTimer = timer
        timer.resume()
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
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[\(slotName)] adb not found: \(adbPath)\n")
            appendLog("[\(slotName)] set full path or install Android platform-tools (example: /opt/homebrew/bin/adb)\n")
            return
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        var args = ["-u", botScript.path, "--plan", scriptURL.path, "--adb", resolvedADB]
        if !device.trimmingCharacters(in: .whitespaces).isEmpty {
            args += ["--device", device.trimmingCharacters(in: .whitespaces)]
        }
        proc.arguments = args
        proc.environment = mergedEnvironment()

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe

        readHandle = pipe.fileHandleForReading
        readHandle?.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { return }
            if let text = String(data: data, encoding: .utf8), let self, self.showRealtimeCommandLogs {
                self.appendLog("[\(self.slotName)] \(text)")
            }
        }

        appendLog("[\(slotName)] start: /usr/bin/python3 \(args.joined(separator: " "))\n")
        do {
            try proc.run()
            process = proc
            isRunning = true
            startProgressTimer(scriptURL: scriptURL)
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
                self.stopProgressTimer()
            }
        } catch {
            appendLog("[\(slotName)] failed to start: \(error)\n")
            readHandle?.readabilityHandler = nil
            readHandle = nil
            process = nil
            isRunning = false
            stopProgressTimer()
        }
    }

    func stop() {
        guard let proc = process else {
            appendLog("[\(slotName)] not running\n")
            return
        }
        appendLog("[\(slotName)] stopping...\n")
        proc.interrupt()
        let procRef = proc
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) {
            if procRef.isRunning {
                procRef.terminate()
            }
        }
    }

    deinit {
        if let proc = process, proc.isRunning {
            proc.terminate()
        }
        stopProgressTimer()
        readHandle?.readabilityHandler = nil
        if let observer = exitObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }
}

final class RecorderModel: ObservableObject {
    private static let maxLogChars = 80_000
    private static let logFlushIntervalSec: Double = 0.12
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
    private let logQueue = DispatchQueue(label: "bs.recorder.log.queue")
    private var pendingLogs: [String] = []
    private var logFlushScheduled = false

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
        logQueue.async {
            self.pendingLogs.append(line)
            guard !self.logFlushScheduled else { return }
            self.logFlushScheduled = true
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.logFlushIntervalSec) { [weak self] in
                self?.flushPendingLogs()
            }
        }
    }

    private func flushPendingLogs() {
        let batch: [String] = logQueue.sync {
            let data = pendingLogs
            pendingLogs.removeAll(keepingCapacity: true)
            logFlushScheduled = false
            return data
        }
        guard !batch.isEmpty else { return }
        let merged = batch.reversed().joined() + logs
        if merged.count > Self.maxLogChars {
            logs = String(merged.prefix(Self.maxLogChars))
        } else {
            logs = merged
        }
    }

    func clearLogs() {
        logQueue.async {
            self.pendingLogs.removeAll(keepingCapacity: false)
            self.logFlushScheduled = false
        }
        DispatchQueue.main.async {
            self.logs = ""
        }
    }

    func start() {
        guard !isRecording else {
            appendLog("[REC] already recording\n")
            return
        }

        let fileName = normalizedOutputName()
        let outputURL = plansDir.appendingPathComponent(fileName)
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[REC] adb not found: \(adbPath)\n")
            appendLog("[REC] set full path or install Android platform-tools (example: /opt/homebrew/bin/adb)\n")
            return
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        let loop = Int(loopCount.trimmingCharacters(in: .whitespacesAndNewlines)) ?? -1
        var args = [
            "-u",
            recorderScript.path,
            "--output", outputURL.path,
            "--adb", resolvedADB,
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
        proc.environment = mergedEnvironment()

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
    private static let maxLogChars = 80_000
    private static let logFlushIntervalSec: Double = 0.12
    @Published var device: String = ""
    @Published var adbPath: String = "adb"
    @Published var logs: String = ""
    private var isBusy = false
    private let logQueue = DispatchQueue(label: "bs.calibration.log.queue")
    private var pendingLogs: [String] = []
    private var logFlushScheduled = false

    func appendLog(_ line: String) {
        logQueue.async {
            self.pendingLogs.append(line)
            guard !self.logFlushScheduled else { return }
            self.logFlushScheduled = true
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.logFlushIntervalSec) { [weak self] in
                self?.flushPendingLogs()
            }
        }
    }

    private func flushPendingLogs() {
        let batch: [String] = logQueue.sync {
            let data = pendingLogs
            pendingLogs.removeAll(keepingCapacity: true)
            logFlushScheduled = false
            return data
        }
        guard !batch.isEmpty else { return }
        let merged = batch.reversed().joined() + logs
        if merged.count > Self.maxLogChars {
            logs = String(merged.prefix(Self.maxLogChars))
        } else {
            logs = merged
        }
    }

    private func scaledPoint(_ fx: Double, _ fy: Double, width: Int, height: Int) -> (Int, Int) {
        let x = max(0, min(width - 1, Int(round(Double(width - 1) * fx))))
        let y = max(0, min(height - 1, Int(round(Double(height - 1) * fy))))
        return (x, y)
    }

    func clearLogs() {
        logQueue.async {
            self.pendingLogs.removeAll(keepingCapacity: false)
            self.logFlushScheduled = false
        }
        DispatchQueue.main.async {
            self.logs = ""
        }
    }

    private func runADB(adbPath: String, device: String, shellArgs: [String], title: String) {
        if isBusy {
            appendLog("[CAL] busy, wait current task\n")
            return
        }
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[CAL] adb not found: \(adbPath)\n")
            appendLog("[CAL] set full path or install Android platform-tools\n")
            return
        }
        isBusy = true
        appendLog("[CAL] \(title)\n")
        DispatchQueue.global().async {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: resolvedADB)
            var args: [String] = []
            let dev = device.trimmingCharacters(in: .whitespacesAndNewlines)
            if !dev.isEmpty {
                args += ["-s", dev]
            }
            args += ["shell"] + shellArgs
            proc.arguments = args
            proc.environment = mergedEnvironment()
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
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[CAL] adb not found: \(adbPath)\n")
            return
        }
        let (w, h) = readScreenSize(adbPath: resolvedADB, device: device) ?? (1080, 1920)
        let (cx, cy) = scaledPoint(0.5, 0.5, width: w, height: h)
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "tap", String(cx), String(cy)],
            title: "Tap Test (\(cx),\(cy)) @ \(w)x\(h)"
        )
    }

    func swipeToBottomLeft(adbPath: String, device: String) {
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[CAL] adb not found: \(adbPath)\n")
            return
        }
        let (w, h) = readScreenSize(adbPath: resolvedADB, device: device) ?? (1080, 1920)
        let (x1, y1) = scaledPoint(0.5, 0.5, width: w, height: h)
        let (x2, y2) = scaledPoint(0.075, 0.96, width: w, height: h)
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "swipe", String(x1), String(y1), String(x2), String(y2), "500"],
            title: "Swipe to Bottom-Left Test"
        )
    }

    func swipeToTopRight(adbPath: String, device: String) {
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[CAL] adb not found: \(adbPath)\n")
            return
        }
        let (w, h) = readScreenSize(adbPath: resolvedADB, device: device) ?? (1080, 1920)
        let (x1, y1) = scaledPoint(0.5, 0.5, width: w, height: h)
        let (x2, y2) = scaledPoint(0.925, 0.04, width: w, height: h)
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "swipe", String(x1), String(y1), String(x2), String(y2), "500"],
            title: "Swipe to Top-Right Test"
        )
    }

    func swipeDownTest(adbPath: String, device: String) {
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[CAL] adb not found: \(adbPath)\n")
            return
        }
        let (w, h) = readScreenSize(adbPath: resolvedADB, device: device) ?? (1080, 1920)
        let (x1, y1) = scaledPoint(0.5, 0.26, width: w, height: h)
        let (x2, y2) = scaledPoint(0.5, 0.73, width: w, height: h)
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "swipe", String(x1), String(y1), String(x2), String(y2), "500"],
            title: "Swipe Down Test"
        )
    }

    func swipeUpTest(adbPath: String, device: String) {
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[CAL] adb not found: \(adbPath)\n")
            return
        }
        let (w, h) = readScreenSize(adbPath: resolvedADB, device: device) ?? (1080, 1920)
        let (x1, y1) = scaledPoint(0.5, 0.73, width: w, height: h)
        let (x2, y2) = scaledPoint(0.5, 0.26, width: w, height: h)
        runADB(
            adbPath: adbPath,
            device: device,
            shellArgs: ["input", "swipe", String(x1), String(y1), String(x2), String(y2), "500"],
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
    @Published var adbStatusMessage: String = "ADB 未检测"
    @Published var availableDevices: [String] = []
    @Published var showRunAfterRecordPrompt: Bool = false
    @Published var lastRecordedScriptToRun: String = ""
    @Published var language: AppLanguage = .zh

    @Published var runnerA = RunnerModel(slotName: "A")
    @Published var runnerB = RunnerModel(slotName: "B")
    @Published var recorder: RecorderModel!
    @Published var calibration = CalibrationModel()
    private let runnerALastScriptKey = "bs.runnerA.lastScript"
    private let runnerBLastScriptKey = "bs.runnerB.lastScript"
    private let lastDeviceKey = "bs.lastDevice"
    private let languageKey = "bs.ui.language"

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
        calibration.adbPath = recorder.adbPath
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
            let devices = refreshResult.devices
            DispatchQueue.main.async {
                self.availableDevices = devices
                if devices.isEmpty {
                    if refreshResult.recovered {
                        self.adbStatusMessage = "ADB 已自动重启 (\(resolvedADB))，但当前无在线 device"
                    } else {
                        self.adbStatusMessage = "ADB 正常 (\(resolvedADB))，但当前无在线 device"
                    }
                } else {
                    if refreshResult.recovered {
                        self.adbStatusMessage = "ADB 已自动重启 (\(resolvedADB))，在线设备: \(devices.count)"
                    } else {
                        self.adbStatusMessage = "ADB 正常 (\(resolvedADB))，在线设备: \(devices.count)"
                    }
                    self.recorder.device = self.preferredDevice(from: devices, current: self.recorder.device)
                    self.runnerA.device = self.preferredDevice(from: devices, current: self.runnerA.device)
                    self.runnerB.device = self.preferredDevice(from: devices, current: self.runnerB.device)
                    self.calibration.device = self.preferredDevice(from: devices, current: self.calibration.device)
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
            let devices = listConnectedDevices(adbPath: resolvedADB)
            DispatchQueue.main.async {
                self.availableDevices = devices
                if devices.isEmpty {
                    self.adbStatusMessage = "ADB 强制重启完成 (\(resolvedADB))，但当前无在线 device"
                } else {
                    self.adbStatusMessage = "ADB 强制重启完成 (\(resolvedADB))，在线设备: \(devices.count)"
                    self.recorder.device = self.preferredDevice(from: devices, current: self.recorder.device)
                    self.runnerA.device = self.preferredDevice(from: devices, current: self.runnerA.device)
                    self.runnerB.device = self.preferredDevice(from: devices, current: self.runnerB.device)
                    self.calibration.device = self.preferredDevice(from: devices, current: self.calibration.device)
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
    let lang: AppLanguage
    let scripts: [String]
    let deviceOptions: [String]
    let refreshDevices: (String) -> Void
    let onDeviceChanged: (String) -> Void
    let resolveScriptURL: (String) -> URL?
    let onSelectionChanged: (String, String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(t(lang, "脚本", "Script"))
                Picker(t(lang, "脚本", "Script"), selection: $runner.selectedScript) {
                    ForEach(scripts, id: \.self) { name in
                        Text(name).tag(name)
                    }
                }
                .frame(width: 240)
                .onChange(of: runner.selectedScript) { newValue in
                    onSelectionChanged(runner.slotName, newValue)
                }
                Button(t(lang, "打开目录", "Open")) {
                    openScriptsDirectoryInFinder()
                }
            }
            HStack {
                Text(t(lang, "设备", "Device"))
                TextField("127.0.0.1:5555", text: $runner.device)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: runner.device) { newValue in
                        onDeviceChanged(newValue)
                    }
                Menu(t(lang, "选择", "Select")) {
                    Button(t(lang, "清空", "Clear")) { runner.device = "" }
                    ForEach(deviceOptions, id: \.self) { serial in
                        Button(serial) {
                            runner.device = serial
                            onDeviceChanged(serial)
                        }
                    }
                }
                Button(t(lang, "刷新设备", "Refresh Devices")) {
                    refreshDevices(runner.adbPath)
                }
            }
            HStack {
                Text(t(lang, "ADB", "ADB"))
                TextField("adb", text: $runner.adbPath)
                    .textFieldStyle(.roundedBorder)
            }
            HStack {
                Button(t(lang, "开始 \(runner.slotName)", "Start \(runner.slotName)")) {
                    runner.start(scriptURL: resolveScriptURL(runner.selectedScript))
                }
                .disabled(runner.isRunning)
                Button(t(lang, "停止 \(runner.slotName)", "Stop \(runner.slotName)")) {
                    runner.stop()
                }
                Button(t(lang, "清空日志", "Clear Logs")) {
                    runner.clearLogs()
                }
            }
            Toggle(
                t(lang, "显示实时指令输出", "Show Realtime Command Output"),
                isOn: $runner.showRealtimeCommandLogs
            )
            HStack {
                Text(t(lang, "进度", "Progress"))
                Text(runner.progressText)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            ScrollView {
                Text(runner.logs.isEmpty ? t(lang, "暂无日志", "No logs") : runner.logs)
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
    let lang: AppLanguage
    let deviceOptions: [String]
    let refreshDevices: (String) -> Void
    let onDeviceChanged: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(t(lang, "输出", "Output"))
                TextField("recorded.json", text: $recorder.outputName)
                    .textFieldStyle(.roundedBorder)
            }
            HStack {
                Text(t(lang, "设备", "Device"))
                TextField("127.0.0.1:5555", text: $recorder.device)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: recorder.device) { newValue in
                        onDeviceChanged(newValue)
                    }
                Menu(t(lang, "选择", "Select")) {
                    Button(t(lang, "清空", "Clear")) { recorder.device = "" }
                    ForEach(deviceOptions, id: \.self) { serial in
                        Button(serial) {
                            recorder.device = serial
                            onDeviceChanged(serial)
                        }
                    }
                }
                Button(t(lang, "刷新设备", "Refresh Devices")) {
                    refreshDevices(recorder.adbPath)
                }
            }
            HStack {
                Text(t(lang, "ADB", "ADB"))
                TextField("adb", text: $recorder.adbPath)
                    .textFieldStyle(.roundedBorder)
                Text(t(lang, "循环", "Loop"))
                TextField("-1", text: $recorder.loopCount)
                    .frame(width: 64)
                    .textFieldStyle(.roundedBorder)
            }
            Toggle(t(lang, "轻度噪声清理", "Clean Noise (Mild)"), isOn: $recorder.cleanNoise)
            Toggle(t(lang, "锁定映射", "Lock Mapping"), isOn: $recorder.mappingLocked)
            HStack {
                Toggle(t(lang, "X 反转", "Invert X"), isOn: $recorder.invertX)
                Toggle(t(lang, "Y 反转", "Invert Y"), isOn: $recorder.invertY)
                Toggle(t(lang, "XY 互换", "Swap XY"), isOn: $recorder.swapXY)
            }
            .disabled(recorder.mappingLocked)
            HStack {
                Button(t(lang, "开始录制", "Start Recording")) {
                    recorder.start()
                }
                .disabled(recorder.isRecording)
                Button(t(lang, "停止录制", "Stop Recording")) {
                    recorder.stop()
                }
                Button(t(lang, "清空日志", "Clear Logs")) {
                    recorder.clearLogs()
                }
            }
            ScrollView {
                Text(recorder.logs.isEmpty ? t(lang, "暂无日志", "No logs") : recorder.logs)
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
    @ObservedObject var calibration: CalibrationModel
    let lang: AppLanguage
    let deviceOptions: [String]
    let refreshDevices: (String) -> Void
    let forceRefreshDevices: (String) -> Void
    let onDeviceChanged: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(t(lang, "设备", "Device"))
                TextField("127.0.0.1:5555", text: $calibration.device)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: calibration.device) { newValue in
                        onDeviceChanged(newValue)
                    }
                Menu(t(lang, "选择", "Select")) {
                    Button(t(lang, "清空", "Clear")) { calibration.device = "" }
                    ForEach(deviceOptions, id: \.self) { serial in
                        Button(serial) {
                            calibration.device = serial
                            onDeviceChanged(serial)
                        }
                    }
                }
                Button(t(lang, "刷新设备", "Refresh Devices")) {
                    refreshDevices(calibration.adbPath)
                }
                Button(t(lang, "强制刷新", "Force Refresh")) {
                    forceRefreshDevices(calibration.adbPath)
                }
            }
            HStack {
                Text(t(lang, "ADB", "ADB"))
                TextField("adb", text: $calibration.adbPath)
                    .textFieldStyle(.roundedBorder)
            }
            HStack {
                Button(t(lang, "连通检查", "Ping")) {
                    calibration.ping(adbPath: calibration.adbPath, device: calibration.device)
                }
                Button(t(lang, "屏幕尺寸", "Screen Size")) {
                    calibration.screenSize(adbPath: calibration.adbPath, device: calibration.device)
                }
                Button(t(lang, "点击测试", "Tap Test")) {
                    calibration.tapTest(adbPath: calibration.adbPath, device: calibration.device)
                }
            }
            HStack {
                Button(t(lang, "滑到左下", "Swipe to Bottom-Left")) {
                    calibration.swipeToBottomLeft(adbPath: calibration.adbPath, device: calibration.device)
                }
                Button(t(lang, "滑到右上", "Swipe to Top-Right")) {
                    calibration.swipeToTopRight(adbPath: calibration.adbPath, device: calibration.device)
                }
            }
            HStack {
                Button(t(lang, "下滑测试", "Swipe Down Test")) {
                    calibration.swipeDownTest(adbPath: calibration.adbPath, device: calibration.device)
                }
                Button(t(lang, "上滑测试", "Swipe Up Test")) {
                    calibration.swipeUpTest(adbPath: calibration.adbPath, device: calibration.device)
                }
                Button(t(lang, "清空日志", "Clear Logs")) {
                    calibration.clearLogs()
                }
            }
            ScrollView {
                Text(calibration.logs.isEmpty ? t(lang, "暂无日志", "No logs") : calibration.logs)
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

enum MainSection: CaseIterable, Identifiable {
    case record
    case run
    case calibration

    var id: String {
        switch self {
        case .record: return "record"
        case .run: return "run"
        case .calibration: return "calibration"
        }
    }

    func title(_ lang: AppLanguage) -> String {
        switch self {
        case .record: return t(lang, "录制", "Record")
        case .run: return t(lang, "运行", "Run")
        case .calibration: return t(lang, "校准", "Calibration")
        }
    }
}

struct ScriptEditorView: View {
    @ObservedObject var model: AppModel
    @Binding var showNewScriptDialog: Bool
    @Binding var newScriptName: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button(t(model.language, "刷新", "Refresh")) {
                    model.refreshScripts()
                }
                Button(t(model.language, "新建", "New")) {
                    newScriptName = ""
                    showNewScriptDialog = true
                }
                Button(t(model.language, "保存", "Save")) {
                    model.saveCurrentScript()
                }
            }
            HStack {
                Picker(t(model.language, "脚本", "Script"), selection: $model.selectedScriptName) {
                    ForEach(model.scripts.map(\.name), id: \.self) { name in
                        Text(name).tag(name)
                    }
                }
                Button(t(model.language, "打开目录", "Open")) {
                    openScriptsDirectoryInFinder()
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
    }
}

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
            HStack {
                Button(t(model.language, "多开运行窗口", "Open Additional Run Window")) {
                    openWindow(id: "run-window", value: buildRunWindowValue())
                }
                Button(t(model.language, "刷新脚本", "Refresh Scripts")) {
                    model.refreshScripts()
                }
                Button(t(model.language, "刷新设备", "Refresh Devices")) {
                    model.refreshADBAndDevices(adbInput: model.recorder.adbPath)
                }
            }
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
                refreshDevices: model.refreshADBAndDevices(adbInput:),
                onDeviceChanged: model.rememberLastDevice(_:),
                resolveScriptURL: model.scriptURL(named:),
                onSelectionChanged: { slot, script in
                    model.rememberRunnerScript(slot: slot, name: script)
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
                refreshDevices: model.refreshADBAndDevices(adbInput:),
                onDeviceChanged: model.rememberLastDevice(_:),
                resolveScriptURL: model.scriptURL(named:),
                onSelectionChanged: { _, _ in }
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

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow
    @State private var selectedSection: MainSection? = .record
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
                    Picker("", selection: Binding(
                        get: { model.language },
                        set: { model.setLanguage($0) }
                    )) {
                        ForEach(AppLanguage.allCases) { lang in
                            Text(lang.displayName).tag(lang)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 100)
                    Spacer()
                    Text(model.adbStatusMessage)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                switch selectedSection ?? .record {
                case .calibration:
                    GroupBox(t(model.language, "校准", "Calibration")) {
                        CalibrationView(
                            calibration: model.calibration,
                            lang: model.language,
                            deviceOptions: model.availableDevices,
                            refreshDevices: model.refreshADBAndDevices(adbInput:),
                            forceRefreshDevices: model.forceRefreshADBAndDevices(adbInput:),
                            onDeviceChanged: model.rememberLastDevice(_:)
                        )
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
                    GroupBox(t(model.language, "脚本编辑", "Script Editor")) {
                        ScriptEditorView(
                            model: model,
                            showNewScriptDialog: $showNewScriptDialog,
                            newScriptName: $newScriptName
                        )
                        .padding(8)
                    }
                case .run:
                    GroupBox(t(model.language, "运行管理", "Run Manager")) {
                        RunHomeView()
                    }
                }
            }
            .padding(12)
        }
        .background(
            WindowAspectRatioGuard(ratio: CGSize(width: 1160, height: 780))
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
        WindowGroup("BSManager") {
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
}
