import SwiftUI
import Foundation

final class CalibrationModel: ObservableObject {
    private static let maxLogChars = 80_000
    private static let logFlushIntervalSec: Double = 0.12
    private static let maxPickedCoordinates = 10
    @Published var device: String = ""
    @Published var adbPath: String = "adb"
    @Published var logs: String = ""
    @Published var detectedScreenSize: String = "未读取"
    @Published var isPickingCoordinates: Bool = false
    @Published var pickedCoordinates: [PickedCoordinate] = []
    private var isBusy = false
    private let logQueue = DispatchQueue(label: "bs.calibration.log.queue")
    private var pendingLogs: [String] = []
    private var logFlushScheduled = false
    private var clickProcess: Process?
    private var clickReadHandle: FileHandle?
    private var clickExitObserver: NSObjectProtocol?
    private var clickOutputBuffer = ""

    private func pushPickedCoordinate(x: Int, y: Int, device: String) {
        DispatchQueue.main.async {
            let item = PickedCoordinate(x: x, y: y, device: device, capturedAt: Date())
            var updated = self.pickedCoordinates
            updated.insert(item, at: 0)
            if updated.count > Self.maxPickedCoordinates {
                updated = Array(updated.prefix(Self.maxPickedCoordinates))
            }
            self.pickedCoordinates = updated
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

    func startCoordinatePicker(adbPath: String, device: String) {
        guard !isPickingCoordinates else {
            appendLog("[CAL] coordinate picker already running\n")
            return
        }
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[CAL] adb not found: \(adbPath)\n")
            return
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        var args = ["-u", recorderScript.path, "--adb", resolvedADB, "--print-clicks-only", "--output", "/tmp/bs_click_probe.json"]
        let dev = device.trimmingCharacters(in: .whitespacesAndNewlines)
        if !dev.isEmpty {
            args += ["--device", dev]
        }
        args += ["--profile", recordingProfileURL(for: device).path]
        proc.arguments = args
        proc.environment = mergedEnvironment()

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        clickOutputBuffer = ""
        clickReadHandle = pipe.fileHandleForReading
        clickReadHandle?.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { return }
            guard let self, let text = String(data: data, encoding: .utf8) else { return }
            self.clickOutputBuffer += text

            let lines = self.clickOutputBuffer.components(separatedBy: "\n")
            let completeLines = lines.dropLast()
            self.clickOutputBuffer = lines.last ?? ""

            if let regex = try? NSRegularExpression(pattern: #"\[Click\]\s+x=(\d+)\s+y=(\d+)"#) {
                for line in completeLines {
                    let nsLine = line as NSString
                    let range = NSRange(location: 0, length: nsLine.length)
                    guard let match = regex.firstMatch(in: line, options: [], range: range),
                          let xRange = Range(match.range(at: 1), in: line),
                          let yRange = Range(match.range(at: 2), in: line),
                          let x = Int(line[xRange]),
                          let y = Int(line[yRange]) else { continue }
                    let currentDevice = device.trimmingCharacters(in: .whitespacesAndNewlines)
                    self.pushPickedCoordinate(x: x, y: y, device: currentDevice)
                }
            }
            self.appendLog("[CAL] \(text)")
        }

        appendLog("[CAL] start coordinate picker\n")
        do {
            try proc.run()
            clickProcess = proc
            isPickingCoordinates = true
            clickExitObserver = NotificationCenter.default.addObserver(
                forName: Process.didTerminateNotification,
                object: proc,
                queue: .main
            ) { [weak self] _ in
                guard let self else { return }
                self.clickReadHandle?.readabilityHandler = nil
                self.clickReadHandle = nil
                self.clickProcess = nil
                self.clickOutputBuffer = ""
                self.isPickingCoordinates = false
                self.appendLog("[CAL] coordinate picker exit: \(proc.terminationStatus)\n")
            }
        } catch {
            appendLog("[CAL] coordinate picker failed: \(error)\n")
            clickReadHandle?.readabilityHandler = nil
            clickReadHandle = nil
            clickProcess = nil
            clickOutputBuffer = ""
            isPickingCoordinates = false
        }
    }

    func stopCoordinatePicker() {
        guard let proc = clickProcess else {
            appendLog("[CAL] coordinate picker not running\n")
            return
        }
        appendLog("[CAL] stopping coordinate picker...\n")
        proc.interrupt()
        let procRef = proc
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) {
            if procRef.isRunning {
                procRef.terminate()
            }
        }
    }

    func clearPickedCoordinates() {
        DispatchQueue.main.async {
            self.pickedCoordinates = []
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
            let dev = device.trimmingCharacters(in: .whitespacesAndNewlines)
            let result = runADBShellCommandWithRecovery(adbPath: resolvedADB, device: dev, shellArgs: shellArgs)
            var args: [String] = []
            if !dev.isEmpty {
                args += ["-s", dev]
            }
            args += ["shell"] + shellArgs
            self.appendLog("[CAL] cmd: \(args.joined(separator: " "))\n")
            if result.recovered {
                self.appendLog("[CAL] adb recovered and retried once\n")
            }
            if !result.text.isEmpty {
                self.appendLog("[CAL] \(result.text)\n")
            }
            self.appendLog("[CAL] exit: \(result.code)\n")
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
        guard let resolvedADB = resolveADBExecutable(adbPath) else { return }
        DispatchQueue.global().async {
            let size = readScreenSize(adbPath: resolvedADB, device: device)
            DispatchQueue.main.async {
                self.detectedScreenSize = size.map { "\($0.0)x\($0.1)" } ?? "读取失败"
            }
        }
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

    deinit {
        clickReadHandle?.readabilityHandler = nil
        if let observer = clickExitObserver {
            NotificationCenter.default.removeObserver(observer)
        }
        if let proc = clickProcess, proc.isRunning {
            proc.terminate()
        }
    }
}
