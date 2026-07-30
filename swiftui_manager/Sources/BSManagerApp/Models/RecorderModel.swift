import SwiftUI
import Foundation

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
            let selectedDevice = device.trimmingCharacters(in: .whitespaces)
            args += ["--device", selectedDevice]
            args += ["--profile", recordingProfileURL(for: selectedDevice).path]
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
