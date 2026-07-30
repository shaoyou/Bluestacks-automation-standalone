import SwiftUI
import Foundation

final class RecordingDiagnosticModel: ObservableObject {
    private static let maxLogChars = 80_000
    private static let logFlushIntervalSec: Double = 0.12
    @Published var device: String = ""
    @Published var adbPath: String = "adb"
    @Published var logs: String = ""
    @Published var isRunning: Bool = false
    @Published var lastApplied: String = ""
    @Published var lastReportPath: String = ""

    private var process: Process?
    private var readHandle: FileHandle?
    private var exitObserver: NSObjectProtocol?
    private let logQueue = DispatchQueue(label: "bs.diagnostic.log.queue")
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
        guard !isRunning else {
            appendLog("[DIA] already running\n")
            return
        }
        guard let resolvedADB = resolveADBExecutable(adbPath) else {
            appendLog("[DIA] adb not found: \(adbPath)\n")
            return
        }

        let reportURL = diagnosticReportURL(for: device)
        let profileURL = recordingProfileURL(for: device)
        try? FileManager.default.createDirectory(at: diagnosticsDir, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: recordingProfilesDir, withIntermediateDirectories: true)

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        var args = [
            "-u",
            recorderScript.path,
            "--output", reportURL.path,
            "--adb", resolvedADB,
            "--diagnose-self-heal",
            "--profile", profileURL.path,
        ]
        let dev = device.trimmingCharacters(in: .whitespacesAndNewlines)
        if !dev.isEmpty {
            args += ["--device", dev]
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
                self.appendLog("[DIA] \(text)")
            }
        }

        appendLog("[DIA] start: /usr/bin/python3 \(args.joined(separator: " "))\n")
        do {
            try proc.run()
            process = proc
            isRunning = true
            lastApplied = ""
            lastReportPath = reportURL.path
            exitObserver = NotificationCenter.default.addObserver(
                forName: Process.didTerminateNotification,
                object: proc,
                queue: .main
            ) { [weak self] _ in
                guard let self else { return }
                self.readHandle?.readabilityHandler = nil
                self.readHandle = nil
                self.isRunning = false
                self.process = nil
                let code = proc.terminationStatus
                self.appendLog("[DIA] exit code: \(code)\n")
                if code == 0 {
                    self.lastApplied = "profile: \(profileURL.lastPathComponent)"
                    self.appendLog("[DIA] profile path: \(profileURL.path)\n")
                    self.appendLog("[DIA] report path: \(reportURL.path)\n")
                }
            }
        } catch {
            appendLog("[DIA] failed to start: \(error)\n")
            readHandle?.readabilityHandler = nil
            readHandle = nil
            process = nil
            isRunning = false
        }
    }

    func stop() {
        guard let proc = process else {
            appendLog("[DIA] not running\n")
            return
        }
        appendLog("[DIA] stopping...\n")
        proc.interrupt()
        let procRef = proc
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) {
            if procRef.isRunning {
                procRef.terminate()
            }
        }
    }

    deinit {
        readHandle?.readabilityHandler = nil
        if let observer = exitObserver {
            NotificationCenter.default.removeObserver(observer)
        }
        if let proc = process, proc.isRunning {
            proc.terminate()
        }
    }
}

private extension Process {
    func waitUntilExitOrReturn() -> Int32 {
        self.waitUntilExit()
        return self.terminationStatus
    }
}

private let drawStatsTimestampFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone.current
    formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
    return formatter
}()

private func drawStatsDate(from rawValue: String) -> Date? {
    drawStatsTimestampFormatter.date(from: rawValue.trimmingCharacters(in: .whitespacesAndNewlines))
}

func drawSessionSummaries() -> [DrawSessionSummary] {
    let fileManager = FileManager.default
    let urls = (try? fileManager.contentsOfDirectory(at: drawStatsDir, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles])) ?? []
    return urls
        .filter { $0.lastPathComponent != "latest_summary.json" && $0.lastPathComponent.hasSuffix("_summary.json") }
        .compactMap { url -> DrawSessionSummary? in
            guard let data = try? Data(contentsOf: url),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            let sessionIDFallback = url.deletingPathExtension().lastPathComponent.replacingOccurrences(of: "_summary", with: "")
            let sessionIDRaw = (object["session_id"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let sessionID = sessionIDRaw.isEmpty ? sessionIDFallback : sessionIDRaw
            let updatedAtText = (object["updated_at"] as? String) ?? ""
            let eventPathString = (object["events_path"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let eventsURL = eventPathString.isEmpty
                ? drawStatsDir.appendingPathComponent("\(sessionID)_events.jsonl")
                : URL(fileURLWithPath: eventPathString)
            return DrawSessionSummary(
                id: sessionID,
                sessionID: sessionID,
                updatedAt: drawStatsDate(from: updatedAtText),
                updatedAtText: updatedAtText,
                drawStartedCount: intValue(from: object["draw_started_count"]),
                targetSeenCount: intValue(from: object["target_seen_count"]),
                targetHitCount: intValue(from: object["target_hit_count"]),
                latestEvent: (object["latest_event"] as? String) ?? "",
                latestDrawType: (object["latest_draw_type"] as? String) ?? "",
                latestMatchedTemplate: (object["latest_matched_template"] as? String) ?? "",
                latestMatchedRoleNote: (object["latest_matched_role_note"] as? String) ?? "",
                roleHitCounts: intDictionary(from: object["role_hit_counts"]),
                roleNotes: stringDictionary(from: object["role_notes"]),
                eventsURL: eventsURL
            )
        }
        .sorted {
            let lhsDate = $0.updatedAt ?? .distantPast
            let rhsDate = $1.updatedAt ?? .distantPast
            if lhsDate != rhsDate {
                return lhsDate > rhsDate
            }
            return $0.sessionID > $1.sessionID
        }
}

func drawEvents(for session: DrawSessionSummary) -> [DrawEventRecord] {
    guard let text = try? String(contentsOf: session.eventsURL, encoding: .utf8) else { return [] }
    return text
        .split(separator: "\n")
        .compactMap { line -> DrawEventRecord? in
            guard let data = line.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            let timestampText = (object["timestamp"] as? String) ?? ""
            let event = (object["event"] as? String) ?? ""
            let drawType = (object["draw_type"] as? String) ?? ""
            let matchedTemplate = (object["matched_template"] as? String) ?? ""
            let matchedRoleNote = (object["matched_role_note"] as? String) ?? ""
            let drawStartedCount = intValue(from: object["draw_started_count"])
            let targetSeenCount = intValue(from: object["target_seen_count"])
            let targetHitCount = intValue(from: object["target_hit_count"])
            let id = "\(session.sessionID)-\(timestampText)-\(event)-\(drawStartedCount)-\(targetHitCount)"
            return DrawEventRecord(
                id: id,
                timestamp: drawStatsDate(from: timestampText),
                timestampText: timestampText,
                event: event,
                drawType: drawType,
                matchedTemplate: matchedTemplate,
                matchedRoleNote: matchedRoleNote,
                drawStartedCount: drawStartedCount,
                targetSeenCount: targetSeenCount,
                targetHitCount: targetHitCount
            )
        }
        .sorted {
            let lhsDate = $0.timestamp ?? .distantPast
            let rhsDate = $1.timestamp ?? .distantPast
            if lhsDate != rhsDate {
                return lhsDate > rhsDate
            }
            return $0.id > $1.id
        }
}

func drawScreenshotPairs(for sessionID: String) -> [DrawScreenshotPair] {
    let indexURL = drawResultPairsDir.appendingPathComponent("index.jsonl")
    guard let text = try? String(contentsOf: indexURL, encoding: .utf8) else { return [] }
    return text
        .split(separator: "\n")
        .compactMap { line -> DrawScreenshotPair? in
            guard let data = line.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            let entrySessionID = (object["session_id"] as? String) ?? ""
            guard entrySessionID == sessionID else { return nil }
            let beforeLabel = (object["before_label"] as? String) ?? ""
            let afterLabel = (object["after_label"] as? String) ?? ""
            let beforePath = (object["before_path"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let afterPath = (object["after_path"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return DrawScreenshotPair(
                id: (object["pair_prefix"] as? String) ?? UUID().uuidString,
                sessionID: entrySessionID,
                pairIndex: intValue(from: object["pair_index"]),
                pairPrefix: (object["pair_prefix"] as? String) ?? "",
                drawType: !beforeLabel.isEmpty ? beforeLabel : afterLabel,
                beforeLabel: beforeLabel,
                beforeURL: beforePath.isEmpty ? nil : URL(fileURLWithPath: beforePath),
                beforeSavedAt: drawStatsDate(from: (object["before_saved_at"] as? String) ?? ""),
                beforeSavedAtText: (object["before_saved_at"] as? String) ?? "",
                afterLabel: afterLabel,
                afterURL: afterPath.isEmpty ? nil : URL(fileURLWithPath: afterPath),
                afterSavedAt: drawStatsDate(from: (object["after_saved_at"] as? String) ?? ""),
                afterSavedAtText: (object["after_saved_at"] as? String) ?? ""
            )
        }
        .sorted {
            if $0.pairIndex != $1.pairIndex {
                return $0.pairIndex > $1.pairIndex
            }
            return $0.pairPrefix > $1.pairPrefix
        }
}
