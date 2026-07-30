import SwiftUI
import Foundation
import AppKit

final class RunnerModel: ObservableObject {
    private static let maxLogChars = 80_000
    private static let logFlushIntervalSec: Double = 0.5
    private static let maxBufferedOutputChars = 80_000
    private static let noisyRealtimeLogPatterns = [
        #"if_image \[\d+/\d+\] template '.+' not matched"#,
        #"CMD adb shell input tap"#,
    ]
    let slotName: String
    @Published var selectedScript: String = ""
    @Published var device: String = ""
    @Published var adbPath: String = "adb"
    @Published var logs: String = ""
    @Published var isStarting: Bool = false
    @Published var isRunning: Bool = false
    @Published var cycleDurationSec: Double = 0
    @Published var cycleProgressSec: Double = 0
    @Published var cycleCount: Int = 0
    @Published var profitPerCycle: String = "0"
    @Published var showRealtimeCommandLogs: Bool = false
    @Published var scriptVariables: [ScriptVariable] = []
    @Published var variableStatusMessage: String = ""

    private var process: Process?
    private var readHandle: FileHandle?
    private var exitObserver: NSObjectProtocol?
    private var progressTimer: DispatchSourceTimer?
    private var progressStartAt: Date?
    private let logQueue = DispatchQueue(label: "bs.runner.log.queue")
    private var pendingLogs: [String] = []
    private var logFlushScheduled = false
    private var bufferedProcessOutput = ""
    private var pendingStartID: UUID?

    init(slotName: String) {
        self.slotName = slotName
    }

    func reloadScriptVariables(scriptURL: URL?) {
        guard let scriptURL else {
            scriptVariables = []
            variableStatusMessage = ""
            return
        }
        scriptVariables = loadScriptVariables(from: scriptURL)
        variableStatusMessage = ""
    }

    func updateScriptVariable(id: UUID, keyPath: WritableKeyPath<ScriptVariable, String>, value: String, scriptURL: URL?) {
        guard let index = scriptVariables.firstIndex(where: { $0.id == id }) else { return }
        scriptVariables[index][keyPath: keyPath] = value
        guard let scriptURL else { return }
        do {
            try saveScriptVariables(scriptVariables, to: scriptURL)
            variableStatusMessage = ""
        } catch {
            variableStatusMessage = "变量保存失败: \(error.localizedDescription)"
        }
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

    private static func isNoisyRealtimeLogLine(_ line: String) -> Bool {
        noisyRealtimeLogPatterns.contains { pattern in
            line.range(of: pattern, options: .regularExpression) != nil
        }
    }

    private func filteredRealtimeOutput(_ text: String) -> String {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
        var kept: [Substring] = []
        for line in lines {
            if !Self.isNoisyRealtimeLogLine(String(line)) {
                kept.append(line)
            }
        }
        let body = kept.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        return body.isEmpty ? "" : body + "\n"
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
            self.bufferedProcessOutput = ""
        }
        DispatchQueue.main.async {
            self.logs = ""
        }
    }

    func copyLogsToPasteboard() {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(logs, forType: .string)
    }

    private func appendBufferedProcessOutput(_ text: String) {
        logQueue.async {
            self.bufferedProcessOutput += text
            if self.bufferedProcessOutput.count > Self.maxBufferedOutputChars {
                self.bufferedProcessOutput = String(self.bufferedProcessOutput.suffix(Self.maxBufferedOutputChars))
            }
        }
    }

    private func takeBufferedProcessOutput() -> String {
        logQueue.sync {
            let text = bufferedProcessOutput
            bufferedProcessOutput = ""
            return text
        }
    }

    var progressText: String {
        guard cycleDurationSec > 0 else { return "--" }
        return String(format: "%.1f / %.1fs", cycleProgressSec, cycleDurationSec)
    }

    var cycleCountText: String {
        guard cycleCount > 0 else { return "--" }
        return "\(cycleCount)"
    }

    var expectedProfitText: String {
        let profit = (Double(profitPerCycle.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0) * Double(max(0, cycleCount))
        return Self.formatDisplayNumber(profit)
    }

    private static func formatDisplayNumber(_ value: Double) -> String {
        guard value.isFinite else { return "0" }
        if value.rounded() == value {
            return String(Int(value))
        }
        return String(format: "%.2f", value)
            .replacingOccurrences(of: #"\\.?0+$"#, with: "", options: .regularExpression)
    }

    private func estimateActionsDuration(_ actions: [Any]) -> Double {
        var total: Double = 0
        for item in actions {
            guard let action = item as? [String: Any], let type = action["type"] as? String else { continue }
            switch type {
            case "wait":
                total += doubleValue(from: action["seconds"])
            case "swipe":
                total += Double(intValue(from: action["duration_ms"])) / 1000.0
            case "trace":
                if let points = action["points"] as? [[String: Any]],
                   let first = points.first,
                   let last = points.last {
                    let t0 = intValue(from: first["t_ms"], default: -1)
                    let t1 = intValue(from: last["t_ms"], default: -1)
                    guard t0 >= 0, t1 >= 0 else { break }
                    total += max(0, Double(t1 - t0) / 1000.0)
                }
            case "sequence":
                if let nested = action["actions"] as? [Any] {
                    total += estimateActionsDuration(nested)
                }
            case "loop":
                if let nested = action["actions"] as? [Any] {
                    let cycle = estimateActionsDuration(nested)
                    let count = intValue(from: action["count"], default: 1)
                    total += count <= 0 ? cycle : cycle * Double(count)
                }
            case "patrol":
                let durationMs = intValue(from: action["duration_ms"], default: 500)
                let legWait = doubleValue(from: action["leg_wait_sec"], default: 0.4)
                let rounds = intValue(from: action["rounds"], default: 1)
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
              let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return 0
        }
        let obj = resolveScriptEnvironmentValue(raw, variables: scriptVariableDictionary(from: planScriptVariables(from: raw))) as? [String: Any]
        guard let obj,
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
            self.cycleCount = 0
        }
    }

    private func startProgressTimer(scriptURL: URL) {
        stopProgressTimer()
        let duration = estimateCycleDuration(scriptURL: scriptURL)
        DispatchQueue.main.async {
            self.cycleDurationSec = duration
            self.cycleProgressSec = 0
            self.cycleCount = duration > 0 ? 1 : 0
        }
        guard duration > 0 else { return }
        progressStartAt = Date()
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global())
        timer.schedule(deadline: .now(), repeating: .milliseconds(500))
        timer.setEventHandler { [weak self] in
            guard let self, let start = self.progressStartAt else { return }
            let elapsed = Date().timeIntervalSince(start)
            let cycle = duration > 0 ? elapsed.truncatingRemainder(dividingBy: duration) : 0
            let count = duration > 0 ? Int(floor(elapsed / duration)) + 1 : 0
            DispatchQueue.main.async {
                self.cycleProgressSec = cycle
                self.cycleCount = max(1, count)
            }
        }
        progressTimer = timer
        timer.resume()
    }

    func start(scriptURL: URL?) {
        guard !isRunning && !isStarting else {
            appendLog("[\(slotName)] already running\n")
            return
        }
        guard let scriptURL else {
            appendLog("[\(slotName)] no script selected\n")
            return
        }
        isStarting = true
        let startID = UUID()
        pendingStartID = startID
        appendLog("[\(slotName)] starting...\n")
        DispatchQueue.main.async { [weak self] in
            self?.startProcessIfNeeded(scriptURL: scriptURL, startID: startID)
        }
    }

    private func startProcessIfNeeded(scriptURL: URL, startID: UUID) {
        guard pendingStartID == startID, isStarting else { return }
        guard FileManager.default.fileExists(atPath: scriptURL.path) else {
            appendLog("[\(slotName)] script not found: \(scriptURL.path)\n")
            isStarting = false
            pendingStartID = nil
            return
        }
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[\(slotName)] adb not found: \(adbPath)\n")
            appendLog("[\(slotName)] set full path or install Android platform-tools (example: /opt/homebrew/bin/adb)\n")
            isStarting = false
            pendingStartID = nil
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
        logQueue.async {
            self.bufferedProcessOutput = ""
        }

        readHandle = pipe.fileHandleForReading
        readHandle?.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { return }
            if let text = String(data: data, encoding: .utf8), let self {
                if self.showRealtimeCommandLogs {
                    let filtered = self.filteredRealtimeOutput(text)
                    if !filtered.isEmpty {
                        self.appendLog("[\(self.slotName)] \(filtered)")
                    }
                } else {
                    self.appendBufferedProcessOutput(text)
                }
            }
        }

        appendLog("[\(slotName)] start: /usr/bin/python3 \(args.joined(separator: " "))\n")
        do {
            try proc.run()
            process = proc
            pendingStartID = nil
            isStarting = false
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
                if code != 0 && !self.showRealtimeCommandLogs {
                    let buffered = self.takeBufferedProcessOutput()
                    if !buffered.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        self.appendLog("[\(self.slotName)] process output:\n\(buffered)\n")
                    }
                }
                self.appendLog("[\(self.slotName)] exit code: \(code)\n")
                self.pendingStartID = nil
                self.isStarting = false
                self.isRunning = false
                self.process = nil
                self.stopProgressTimer()
            }
        } catch {
            appendLog("[\(slotName)] failed to start: \(error)\n")
            readHandle?.readabilityHandler = nil
            readHandle = nil
            process = nil
            pendingStartID = nil
            isStarting = false
            isRunning = false
            stopProgressTimer()
        }
    }

    func stop() {
        if isStarting && process == nil {
            appendLog("[\(slotName)] startup cancelled\n")
            pendingStartID = nil
            isStarting = false
            return
        }
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
