import SwiftUI
import Foundation
import AppKit
import UniformTypeIdentifiers

private func workspaceMarkerExists(at directory: URL) -> Bool {
    let fileManager = FileManager.default
    return fileManager.fileExists(atPath: directory.appendingPathComponent("plans", isDirectory: true).path)
        && fileManager.fileExists(atPath: directory.appendingPathComponent("adb_bot.py").path)
        && fileManager.fileExists(atPath: directory.appendingPathComponent("record_touch.py").path)
}

private func synchronizedRuntimeRoot(from bundledRoot: URL) -> URL? {
    let fileManager = FileManager.default
    guard let supportBase = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
        return nil
    }
    let destinationRoot = supportBase
        .appendingPathComponent("BSManagerApp", isDirectory: true)
        .appendingPathComponent("runtime", isDirectory: true)

    try? fileManager.createDirectory(at: destinationRoot, withIntermediateDirectories: true)

    let filesToOverwrite = ["adb_bot.py", "record_touch.py"]
    for name in filesToOverwrite {
        let source = bundledRoot.appendingPathComponent(name)
        let destination = destinationRoot.appendingPathComponent(name)
        if fileManager.fileExists(atPath: destination.path) {
            try? fileManager.removeItem(at: destination)
        }
        if fileManager.fileExists(atPath: source.path) {
            try? fileManager.copyItem(at: source, to: destination)
        }
    }

    let directoriesToMerge = ["plans", "image_templates"]
    for name in directoriesToMerge {
        let sourceDir = bundledRoot.appendingPathComponent(name, isDirectory: true)
        let destinationDir = destinationRoot.appendingPathComponent(name, isDirectory: true)
        guard fileManager.fileExists(atPath: sourceDir.path) else { continue }
        try? fileManager.createDirectory(at: destinationDir, withIntermediateDirectories: true)
        let children = (try? fileManager.contentsOfDirectory(at: sourceDir, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles])) ?? []
        for child in children {
            let destinationChild = destinationDir.appendingPathComponent(child.lastPathComponent, isDirectory: false)
            guard !fileManager.fileExists(atPath: destinationChild.path) else { continue }
            try? fileManager.copyItem(at: child, to: destinationChild)
        }
    }

    let directoriesToEnsure = ["diagnostics", "recording_profiles"]
    for name in directoriesToEnsure {
        try? fileManager.createDirectory(
            at: destinationRoot.appendingPathComponent(name, isDirectory: true),
            withIntermediateDirectories: true
        )
    }

    return workspaceMarkerExists(at: destinationRoot) ? destinationRoot : nil
}

private func findAppRoot(startingAt directory: URL?) -> URL? {
    guard var current = directory?.resolvingSymlinksInPath() else { return nil }
    for _ in 0..<10 {
        if workspaceMarkerExists(at: current) {
            return current
        }
        let parent = current.deletingLastPathComponent()
        if parent.path == current.path {
            break
        }
        current = parent
    }
    return nil
}

private func resolveAppRoot() -> URL {
    if let resourceURL = Bundle.main.resourceURL {
        let bundledRuntime = resourceURL.appendingPathComponent("Runtime", isDirectory: true)
        if workspaceMarkerExists(at: bundledRuntime) {
            return synchronizedRuntimeRoot(from: bundledRuntime) ?? bundledRuntime
        }
    }

    if let executableDirectory = Bundle.main.executableURL?.deletingLastPathComponent() {
        let siblingRuntime = executableDirectory.appendingPathComponent("runtime", isDirectory: true)
        if workspaceMarkerExists(at: siblingRuntime) {
            return siblingRuntime
        }
    }

    let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
    let candidates = [
        cwd,
        Bundle.main.executableURL?.deletingLastPathComponent(),
    ]
    for candidate in candidates {
        if let found = findAppRoot(startingAt: candidate) {
            return found
        }
    }
    return cwd
}

private let appRoot = resolveAppRoot()
private let plansDir = appRoot.appendingPathComponent("plans", isDirectory: true)
private let imageTemplatesDir = appRoot.appendingPathComponent("image_templates", isDirectory: true)
private let recordingProfilesDir = appRoot.appendingPathComponent("recording_profiles", isDirectory: true)
private let diagnosticsDir = appRoot.appendingPathComponent("diagnostics", isDirectory: true)
private let drawStatsDir = diagnosticsDir.appendingPathComponent("draw_stats", isDirectory: true)
private let drawResultPairsDir = diagnosticsDir.appendingPathComponent("draw_result_pairs", isDirectory: true)
private let botScript = appRoot.appendingPathComponent("adb_bot.py")
private let recorderScript = appRoot.appendingPathComponent("record_touch.py")
private let appVersion = "1.1.0"
private let mainWindowSceneID = "main-window"
private let mainWindowIdentifier = NSUserInterfaceItemIdentifier(mainWindowSceneID)
private let scriptEnvironmentVariablesKey = "bs.scriptEnvironmentVariables"
private let scriptEnvironmentReferenceRegex = try! NSRegularExpression(pattern: #"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)"#)

struct ScriptEnvironmentVariable: Identifiable, Codable, Equatable {
    var id: UUID = UUID()
    var name: String
    var value: String
    var note: String
}

private func loadScriptEnvironmentVariables() -> [ScriptEnvironmentVariable] {
    guard let data = UserDefaults.standard.data(forKey: scriptEnvironmentVariablesKey),
          let variables = try? JSONDecoder().decode([ScriptEnvironmentVariable].self, from: data) else {
        return []
    }
    return variables
}

private func saveScriptEnvironmentVariables(_ variables: [ScriptEnvironmentVariable]) {
    guard let data = try? JSONEncoder().encode(variables) else { return }
    UserDefaults.standard.set(data, forKey: scriptEnvironmentVariablesKey)
}

private func scriptEnvironmentDictionary(from variables: [ScriptEnvironmentVariable]) -> [String: String] {
    var resolved: [String: String] = [:]
    for item in variables {
        let name = item.name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { continue }
        resolved[name] = item.value
    }
    return resolved
}

private func currentScriptEnvironmentDictionary() -> [String: String] {
    scriptEnvironmentDictionary(from: loadScriptEnvironmentVariables())
}

private func scriptEnvironmentName(in text: String, match: NSTextCheckingResult) -> String? {
    if let range = Range(match.range(at: 1), in: text) {
        return String(text[range])
    }
    if let range = Range(match.range(at: 2), in: text) {
        return String(text[range])
    }
    return nil
}

private func parseScriptEnvironmentLiteral(_ rawValue: String) -> Any {
    let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty, let data = trimmed.data(using: .utf8) else {
        return rawValue
    }
    if let parsed = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) {
        return parsed
    }
    return rawValue
}

private func resolveScriptEnvironmentString(_ text: String, variables: [String: String], strict: Bool = false) -> Any {
    let fullRange = NSRange(text.startIndex..<text.endIndex, in: text)
    let matches = scriptEnvironmentReferenceRegex.matches(in: text, options: [], range: fullRange)
    guard !matches.isEmpty else { return text }

    if matches.count == 1,
       matches[0].range.location == 0,
       matches[0].range.length == fullRange.length,
       let name = scriptEnvironmentName(in: text, match: matches[0]) {
        guard let rawValue = variables[name] else {
            return strict ? text : text
        }
        return parseScriptEnvironmentLiteral(rawValue)
    }

    let mutable = NSMutableString(string: text)
    for match in matches.reversed() {
        guard let name = scriptEnvironmentName(in: text, match: match) else { continue }
        guard let rawValue = variables[name] else { continue }
        mutable.replaceCharacters(in: match.range, with: rawValue)
    }
    return String(mutable)
}

private func resolveScriptEnvironmentValue(_ value: Any, variables: [String: String], strict: Bool = false) -> Any {
    switch value {
    case let dict as [String: Any]:
        var resolved: [String: Any] = [:]
        for (key, item) in dict {
            resolved[key] = resolveScriptEnvironmentValue(item, variables: variables, strict: strict)
        }
        return resolved
    case let list as [Any]:
        return list.map { resolveScriptEnvironmentValue($0, variables: variables, strict: strict) }
    case let text as String:
        return resolveScriptEnvironmentString(text, variables: variables, strict: strict)
    default:
        return value
    }
}

private func doubleValue(from rawValue: Any?, default defaultValue: Double = 0) -> Double {
    switch rawValue {
    case let value as Double:
        return value
    case let value as Float:
        return Double(value)
    case let value as Int:
        return Double(value)
    case let value as NSNumber:
        return value.doubleValue
    case let value as String:
        return Double(value.trimmingCharacters(in: .whitespacesAndNewlines)) ?? defaultValue
    default:
        return defaultValue
    }
}

private func intValue(from rawValue: Any?, default defaultValue: Int = 0) -> Int {
    switch rawValue {
    case let value as Int:
        return value
    case let value as Double:
        return Int(value)
    case let value as Float:
        return Int(value)
    case let value as NSNumber:
        return value.intValue
    case let value as String:
        return Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) ?? defaultValue
    default:
        return defaultValue
    }
}

private func mergedEnvironment() -> [String: String] {
    var env = ProcessInfo.processInfo.environment
    for (name, value) in currentScriptEnvironmentDictionary() {
        env[name] = value
    }
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

private func adbOutputNeedsRecovery(code: Int32, text: String) -> Bool {
    let lowered = text.lowercased()
    if code != 0 {
        return true
    }
    return lowered.contains("device offline")
        || lowered.contains("device not found")
        || lowered.contains("more than one device")
        || lowered.contains("cannot connect")
        || lowered.contains("error: closed")
        || lowered.contains("failed to check server version")
        || lowered.contains("adb server didn't ack")
}

private func recoverADBServer(adbPath: String) {
    _ = runADBCommand(adbPath: adbPath, args: ["kill-server"])
    _ = runADBCommand(adbPath: adbPath, args: ["start-server"])
}

private func runADBShellCommandWithRecovery(adbPath: String, device: String, shellArgs: [String]) -> (code: Int32, text: String, recovered: Bool) {
    let trimmed = device.trimmingCharacters(in: .whitespacesAndNewlines)
    var args: [String] = []
    if !trimmed.isEmpty {
        args += ["-s", trimmed]
    }
    args += ["shell"] + shellArgs
    let first = runADBCommand(adbPath: adbPath, args: args)
    if !adbOutputNeedsRecovery(code: first.code, text: first.text) {
        return (first.code, first.text, false)
    }

    if !trimmed.isEmpty {
        _ = runADBCommand(adbPath: adbPath, args: ["connect", trimmed])
    }
    recoverADBServer(adbPath: adbPath)
    let second = runADBCommand(adbPath: adbPath, args: args)
    return (second.code, second.text, true)
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

private func isADBDeviceReachable(adbPath: String, device: String) -> Bool {
    let trimmed = device.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return false }
    let result = runADBShellCommandWithRecovery(
        adbPath: adbPath,
        device: trimmed,
        shellArgs: ["getprop", "ro.build.version.release"]
    )
    let text = result.text.lowercased()
    if adbOutputNeedsRecovery(code: result.code, text: text) {
        return false
    }
    return !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
}

private func splitDevicesByReachability(adbPath: String, devices: [String]) -> (healthy: [String], unhealthy: [String]) {
    var healthy: [String] = []
    var unhealthy: [String] = []
    for device in devices {
        if isADBDeviceReachable(adbPath: adbPath, device: device) {
            healthy.append(device)
        } else {
            unhealthy.append(device)
        }
    }
    return (healthy, unhealthy)
}

private func openScriptsDirectoryInFinder() {
    NSWorkspace.shared.open(plansDir)
}

private func openImageTemplatesDirectoryInFinder() {
    try? FileManager.default.createDirectory(at: imageTemplatesDir, withIntermediateDirectories: true)
    NSWorkspace.shared.open(imageTemplatesDir)
}

private func openDrawResultPairsDirectoryInFinder() {
    try? FileManager.default.createDirectory(at: drawResultPairsDir, withIntermediateDirectories: true)
    NSWorkspace.shared.open(drawResultPairsDir)
}

private func imageTemplateReferencePath(for fileURL: URL) -> String {
    let resolvedFile = fileURL.resolvingSymlinksInPath()
    let resolvedDirectory = imageTemplatesDir.resolvingSymlinksInPath()
    let directoryPath = resolvedDirectory.path
    let filePath = resolvedFile.path
    if filePath.hasPrefix(directoryPath + "/") {
        let relative = String(filePath.dropFirst(directoryPath.count + 1))
        return "../image_templates/\(relative)"
    }
    return filePath
}

private func uniqueFileURL(in directory: URL, preferredName: String) -> URL {
    let fileManager = FileManager.default
    let ext = (preferredName as NSString).pathExtension
    let base = (preferredName as NSString).deletingPathExtension
    var candidate = directory.appendingPathComponent(preferredName)
    var index = 2
    while fileManager.fileExists(atPath: candidate.path) {
        let name = ext.isEmpty ? "\(base)-\(index)" : "\(base)-\(index).\(ext)"
        candidate = directory.appendingPathComponent(name)
        index += 1
    }
    return candidate
}

private func importImageTemplateWithOpenPanel() throws -> String? {
    let panel = NSOpenPanel()
    panel.canChooseFiles = true
    panel.canChooseDirectories = false
    panel.allowsMultipleSelection = false
    panel.allowedContentTypes = [.image]
    guard panel.runModal() == .OK, let sourceURL = panel.url else {
        return nil
    }

    let fileManager = FileManager.default
    try fileManager.createDirectory(at: imageTemplatesDir, withIntermediateDirectories: true)

    let sourceDirectory = sourceURL.resolvingSymlinksInPath().deletingLastPathComponent().path
    let templatesDirectory = imageTemplatesDir.resolvingSymlinksInPath().path
    let destinationURL: URL
    if sourceDirectory == templatesDirectory {
        destinationURL = imageTemplatesDir.appendingPathComponent(sourceURL.lastPathComponent)
    } else {
        destinationURL = uniqueFileURL(in: imageTemplatesDir, preferredName: sourceURL.lastPathComponent)
        try fileManager.copyItem(at: sourceURL, to: destinationURL)
    }
    return imageTemplateReferencePath(for: destinationURL)
}

private func chooseImageTemplateWithOpenPanel() throws -> String? {
    try FileManager.default.createDirectory(at: imageTemplatesDir, withIntermediateDirectories: true)
    let panel = NSOpenPanel()
    panel.canChooseFiles = true
    panel.canChooseDirectories = false
    panel.allowsMultipleSelection = false
    panel.allowedContentTypes = [.image]
    panel.directoryURL = imageTemplatesDir
    guard panel.runModal() == .OK, let selectedURL = panel.url else {
        return nil
    }
    return imageTemplateReferencePath(for: selectedURL)
}

private func sanitizedDeviceFileName(_ device: String) -> String {
    let trimmed = device.trimmingCharacters(in: .whitespacesAndNewlines)
    let base = trimmed.isEmpty ? "default" : trimmed
    let safeScalars = base.unicodeScalars.map { scalar -> Character in
        if CharacterSet.alphanumerics.contains(scalar) || scalar == "-" || scalar == "_" {
            return Character(scalar)
        }
        return "_"
    }
    let safe = String(safeScalars)
    return safe.isEmpty ? "default" : safe
}

private func recordingProfileURL(for device: String) -> URL {
    recordingProfilesDir.appendingPathComponent("\(sanitizedDeviceFileName(device)).json")
}

private func diagnosticReportURL(for device: String) -> URL {
    diagnosticsDir.appendingPathComponent("\(sanitizedDeviceFileName(device))_diagnostic.json")
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

struct PickedCoordinate: Identifiable, Equatable {
    let id = UUID()
    let x: Int
    let y: Int
    let device: String
    let capturedAt: Date
}

struct EditorInsertionRequest: Identifiable, Equatable {
    let id = UUID()
    let text: String
}

struct DeviceScreenshotCapture: Identifiable {
    let id = UUID()
    let image: NSImage
    let cgImage: CGImage
    let pixelSize: CGSize
    let defaultTemplateName: String
}

struct ImageSearchRegion: Equatable {
    let x: Int
    let y: Int
    let width: Int
    let height: Int

    init(x: Int, y: Int, width: Int, height: Int) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }

    init(rect: CGRect) {
        self.x = Int(rect.minX.rounded())
        self.y = Int(rect.minY.rounded())
        self.width = Int(rect.width.rounded())
        self.height = Int(rect.height.rounded())
    }

    var rect: CGRect {
        CGRect(x: x, y: y, width: width, height: height)
    }

    var summaryText: String {
        "x=\(x), y=\(y), w=\(width), h=\(height)"
    }
}

struct DrawSessionSummary: Identifiable, Hashable {
    let id: String
    let sessionID: String
    let updatedAt: Date?
    let updatedAtText: String
    let drawStartedCount: Int
    let targetHitCount: Int
    let latestEvent: String
    let latestDrawType: String
    let latestMatchedTemplate: String
    let eventsURL: URL

    var hitRateText: String {
        guard drawStartedCount > 0 else { return "0%" }
        let value = Double(targetHitCount) / Double(drawStartedCount) * 100.0
        return String(format: "%.1f%%", value)
    }
}

struct DrawEventRecord: Identifiable, Hashable {
    let id: String
    let timestamp: Date?
    let timestampText: String
    let event: String
    let drawType: String
    let matchedTemplate: String
    let drawStartedCount: Int
    let targetHitCount: Int
}

struct DrawScreenshotPair: Identifiable, Hashable {
    let id: String
    let sessionID: String
    let pairIndex: Int
    let pairPrefix: String
    let drawType: String
    let beforeLabel: String
    let beforeURL: URL?
    let beforeSavedAt: Date?
    let beforeSavedAtText: String
    let afterLabel: String
    let afterURL: URL?
    let afterSavedAt: Date?
    let afterSavedAtText: String
}

private struct AppOperationError: LocalizedError {
    let message: String

    var errorDescription: String? { message }
}

private func sanitizedTemplateFileName(_ rawPath: String) -> String {
    let candidate = URL(fileURLWithPath: rawPath).lastPathComponent.trimmingCharacters(in: .whitespacesAndNewlines)
    let fallback = "template-\(Int(Date().timeIntervalSince1970)).png"
    var name = candidate.isEmpty ? fallback : candidate
    name = name.replacingOccurrences(of: #"[^\p{L}\p{N}._-]+"#, with: "_", options: .regularExpression)
    if name.isEmpty {
        name = fallback
    }
    let ext = (name as NSString).pathExtension.lowercased()
    if ext.isEmpty {
        name += ".png"
    } else if ext != "png" {
        name = ((name as NSString).deletingPathExtension) + ".png"
    }
    return name
}

private func captureDeviceScreenshot(adbPath: String, device: String, suggestedTemplatePath: String) throws -> DeviceScreenshotCapture {
    guard let resolvedADB = resolveADBExecutable(adbPath) else {
        throw AppOperationError(message: "未找到 adb: \(adbPath)")
    }

    let proc = Process()
    let outPipe = Pipe()
    let errPipe = Pipe()
    proc.executableURL = URL(fileURLWithPath: resolvedADB)
    var args: [String] = []
    let trimmedDevice = device.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmedDevice.isEmpty {
        args += ["-s", trimmedDevice]
    }
    args += ["exec-out", "screencap", "-p"]
    proc.arguments = args
    proc.environment = mergedEnvironment()
    proc.standardOutput = outPipe
    proc.standardError = errPipe

    var imageData = Data()
    var errorData = Data()
    let readGroup = DispatchGroup()
    readGroup.enter()
    DispatchQueue.global(qos: .userInitiated).async {
        imageData = outPipe.fileHandleForReading.readDataToEndOfFile()
        readGroup.leave()
    }
    readGroup.enter()
    DispatchQueue.global(qos: .utility).async {
        errorData = errPipe.fileHandleForReading.readDataToEndOfFile()
        readGroup.leave()
    }

    do {
        try proc.run()
        proc.waitUntilExit()
    } catch {
        throw AppOperationError(message: "截图命令启动失败: \(error.localizedDescription)")
    }
    readGroup.wait()

    let errorText = String(data: errorData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    guard proc.terminationStatus == 0 else {
        throw AppOperationError(message: errorText.isEmpty ? "截图失败，adb exit code \(proc.terminationStatus)" : errorText)
    }
    let pngHeader = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    guard imageData.starts(with: pngHeader) else {
        throw AppOperationError(message: "设备截图未返回 PNG 数据")
    }
    guard let rep = NSBitmapImageRep(data: imageData), let cgImage = rep.cgImage else {
        throw AppOperationError(message: "设备截图解码失败")
    }
    let nsImage = NSImage(cgImage: cgImage, size: NSSize(width: cgImage.width, height: cgImage.height))
    return DeviceScreenshotCapture(
        image: nsImage,
        cgImage: cgImage,
        pixelSize: CGSize(width: cgImage.width, height: cgImage.height),
        defaultTemplateName: sanitizedTemplateFileName(suggestedTemplatePath)
    )
}

private func saveCroppedTemplateImage(source: CGImage, cropRect: CGRect, preferredName: String) throws -> String {
    try FileManager.default.createDirectory(at: imageTemplatesDir, withIntermediateDirectories: true)
    let bounds = CGRect(x: 0, y: 0, width: source.width, height: source.height)
    let normalized = cropRect.integral.intersection(bounds)
    guard normalized.width >= 1, normalized.height >= 1 else {
        throw AppOperationError(message: "裁剪区域无效")
    }
    guard let cropped = source.cropping(to: normalized) else {
        throw AppOperationError(message: "模板裁剪失败")
    }
    let rep = NSBitmapImageRep(cgImage: cropped)
    guard let pngData = rep.representation(using: .png, properties: [:]) else {
        throw AppOperationError(message: "模板 PNG 编码失败")
    }
    let destination = uniqueFileURL(in: imageTemplatesDir, preferredName: sanitizedTemplateFileName(preferredName))
    try pngData.write(to: destination)
    return "../image_templates/\(destination.lastPathComponent)"
}

final class RunnerModel: ObservableObject {
    private static let maxLogChars = 80_000
    private static let logFlushIntervalSec: Double = 0.12
    private static let maxBufferedOutputChars = 20_000
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
            self.bufferedProcessOutput = ""
        }
        DispatchQueue.main.async {
            self.logs = ""
        }
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
        let obj = resolveScriptEnvironmentValue(raw, variables: currentScriptEnvironmentDictionary()) as? [String: Any]
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
        timer.schedule(deadline: .now(), repeating: .milliseconds(200))
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
                    self.appendLog("[\(self.slotName)] \(text)")
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

final class CalibrationModel: ObservableObject {
    private static let maxLogChars = 80_000
    private static let logFlushIntervalSec: Double = 0.12
    private static let maxPickedCoordinates = 10
    @Published var device: String = ""
    @Published var adbPath: String = "adb"
    @Published var logs: String = ""
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

private func drawSessionSummaries() -> [DrawSessionSummary] {
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
                targetHitCount: intValue(from: object["target_hit_count"]),
                latestEvent: (object["latest_event"] as? String) ?? "",
                latestDrawType: (object["latest_draw_type"] as? String) ?? "",
                latestMatchedTemplate: (object["latest_matched_template"] as? String) ?? "",
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

private func drawEvents(for session: DrawSessionSummary) -> [DrawEventRecord] {
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
            let drawStartedCount = intValue(from: object["draw_started_count"])
            let targetHitCount = intValue(from: object["target_hit_count"])
            let id = "\(session.sessionID)-\(timestampText)-\(event)-\(drawStartedCount)-\(targetHitCount)"
            return DrawEventRecord(
                id: id,
                timestamp: drawStatsDate(from: timestampText),
                timestampText: timestampText,
                event: event,
                drawType: drawType,
                matchedTemplate: matchedTemplate,
                drawStartedCount: drawStartedCount,
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

private func drawScreenshotPairs(for sessionID: String) -> [DrawScreenshotPair] {
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
    @Published var scriptEnvironmentVariables: [ScriptEnvironmentVariable] = loadScriptEnvironmentVariables() {
        didSet {
            saveScriptEnvironmentVariables(scriptEnvironmentVariables)
        }
    }

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

    func addScriptEnvironmentVariable() {
        scriptEnvironmentVariables.append(ScriptEnvironmentVariable(name: "", value: "", note: ""))
    }

    func removeScriptEnvironmentVariable(id: UUID) {
        scriptEnvironmentVariables.removeAll { $0.id == id }
    }

    func updateScriptEnvironmentVariable(id: UUID, keyPath: WritableKeyPath<ScriptEnvironmentVariable, String>, value: String) {
        guard let index = scriptEnvironmentVariables.firstIndex(where: { $0.id == id }) else { return }
        scriptEnvironmentVariables[index][keyPath: keyPath] = value
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
                Text("\(t(lang, "循环次数", "Loop Count")): \(runner.cycleCountText)")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            HStack {
                Text(t(lang, "单次循环收益", "Profit Per Cycle"))
                TextField("0", text: $runner.profitPerCycle)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 120)
                Text("\(t(lang, "预期收益", "Expected Profit")): \(runner.expectedProfitText)")
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
                Button(t(lang, "开始取点", "Start Picking")) {
                    calibration.startCoordinatePicker(adbPath: calibration.adbPath, device: calibration.device)
                }
                .disabled(calibration.isPickingCoordinates)
                Button(t(lang, "停止取点", "Stop Picking")) {
                    calibration.stopCoordinatePicker()
                }
                .disabled(!calibration.isPickingCoordinates)
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
    case run
    case draw
    case record
    case editor
    case settings

    var id: String {
        switch self {
        case .run: return "run"
        case .draw: return "draw"
        case .record: return "record"
        case .editor: return "editor"
        case .settings: return "settings"
        }
    }

    func title(_ lang: AppLanguage) -> String {
        switch self {
        case .run: return t(lang, "运行", "Run")
        case .draw: return t(lang, "抽卡", "Draw")
        case .record: return t(lang, "录制", "Record")
        case .editor: return t(lang, "编辑", "Editor")
        case .settings: return t(lang, "设置", "Settings")
        }
    }
}

private struct DrawMetricCard: View {
    let title: String
    let value: String
    let detail: String?

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.system(size: 24, weight: .semibold, design: .rounded))
                if let detail, !detail.isEmpty {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
        }
    }
}

private struct DrawLocalImageView: View {
    let title: String
    let url: URL?
    let placeholder: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.footnote)
                .foregroundStyle(.secondary)
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color(nsColor: .textBackgroundColor))
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.gray.opacity(0.35), lineWidth: 1)
                if let url, let image = NSImage(contentsOf: url) {
                    Image(nsImage: image)
                        .resizable()
                        .interpolation(.high)
                        .aspectRatio(contentMode: .fit)
                        .padding(8)
                } else {
                    Text(placeholder)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct DrawHistoryView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var runner: RunnerModel
    @State private var sessions: [DrawSessionSummary] = []
    @State private var selectedSessionID: String?
    @State private var events: [DrawEventRecord] = []
    @State private var pairs: [DrawScreenshotPair] = []
    @State private var selectedPairID: String?
    private let refreshTimer = Timer.publish(every: 2.0, on: .main, in: .common).autoconnect()

    private var selectedSession: DrawSessionSummary? {
        sessions.first { $0.sessionID == selectedSessionID }
    }

    private var selectedPair: DrawScreenshotPair? {
        pairs.first { $0.id == selectedPairID }
    }

    private var currentSession: DrawSessionSummary? {
        sessions.first
    }

    private var drawTypeSummary: (min: Int, max: Int) {
        let drawEventsOnly = events.filter { $0.event == "draw_started" }
        let minCount = drawEventsOnly.filter { $0.drawType == "min" }.count
        let maxCount = drawEventsOnly.filter { $0.drawType == "max" }.count
        return (minCount, maxCount)
    }

    private var currentScriptName: String {
        model.drawScriptName()
    }

    private var drawRunnerStatusText: String {
        if runner.isStarting {
            return t(model.language, "启动中", "Starting")
        }
        if runner.isRunning {
            return t(model.language, "运行中", "Running")
        }
        return t(model.language, "空闲", "Idle")
    }

    private func eventTitle(_ event: String) -> String {
        switch event {
        case "draw_started":
            return t(model.language, "开启抽卡", "Draw Started")
        case "target_hit":
            return t(model.language, "命中目标卡", "Target Hit")
        default:
            return event
        }
    }

    private func sessionSubtitle(_ session: DrawSessionSummary) -> String {
        let updated = session.updatedAtText.isEmpty ? session.sessionID : session.updatedAtText
        return t(model.language, "更新于 ", "Updated ") + updated
    }

    private func pairTitle(_ pair: DrawScreenshotPair) -> String {
        let drawType = pair.drawType.isEmpty ? t(model.language, "记录", "Record") : pair.drawType.uppercased()
        return "\(drawType) #\(pair.pairIndex)"
    }

    private func loadSelectedSession() {
        guard let selectedSession else {
            events = []
            pairs = []
            selectedPairID = nil
            return
        }
        events = drawEvents(for: selectedSession)
        pairs = drawScreenshotPairs(for: selectedSession.sessionID)
        if let selectedPairID, pairs.contains(where: { $0.id == selectedPairID }) {
            return
        }
        selectedPairID = pairs.first?.id
    }

    private func reload() {
        sessions = drawSessionSummaries()
        if let selectedSessionID, sessions.contains(where: { $0.sessionID == selectedSessionID }) {
            self.selectedSessionID = selectedSessionID
        } else {
            self.selectedSessionID = sessions.first?.sessionID
        }
        loadSelectedSession()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(t(model.language, "抽卡面板", "Draw Console"))
                    .font(.title3)
                    .fontWeight(.semibold)
                Spacer()
                Button(t(model.language, "打开截图目录", "Open Screenshot Folder")) {
                    openDrawResultPairsDirectoryInFinder()
                }
                Button(t(model.language, "刷新", "Refresh")) {
                    reload()
                }
            }

            GroupBox(t(model.language, "开始抽卡", "Start Draw")) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(t(model.language, "脚本", "Script"))
                        Text(currentScriptName.isEmpty ? "choukaka.json" : currentScriptName)
                            .font(.system(size: 12, weight: .semibold, design: .monospaced))
                        Spacer()
                        Text(drawRunnerStatusText)
                            .foregroundStyle((runner.isRunning || runner.isStarting) ? .orange : .secondary)
                    }

                    HStack {
                        Text(t(model.language, "设备", "Device"))
                        TextField("emulator-5554", text: $runner.device)
                            .textFieldStyle(.roundedBorder)
                            .onChange(of: runner.device) { newValue in
                                model.rememberLastDevice(newValue)
                            }
                        Menu(t(model.language, "选择", "Select")) {
                            Button(t(model.language, "清空", "Clear")) { runner.device = "" }
                            ForEach(model.availableDevices, id: \.self) { serial in
                                Button(serial) {
                                    runner.device = serial
                                    model.rememberLastDevice(serial)
                                }
                            }
                        }
                        Button(t(model.language, "刷新设备", "Refresh Devices")) {
                            model.refreshADBAndDevices(adbInput: model.recorder.adbPath)
                        }
                    }

                    HStack {
                        Button(runner.isStarting ? t(model.language, "启动中...", "Starting...") : t(model.language, "开始抽卡", "Start Draw")) {
                            model.startDrawRun()
                            reload()
                        }
                        .disabled(runner.isRunning || runner.isStarting)
                        Button(t(model.language, "停止抽卡", "Stop Draw")) {
                            runner.stop()
                        }
                        Button(t(model.language, "清空日志", "Clear Logs")) {
                            runner.clearLogs()
                        }
                        Toggle(t(model.language, "显示实时日志", "Show Realtime Logs"), isOn: $runner.showRealtimeCommandLogs)
                            .toggleStyle(.checkbox)
                        Spacer()
                    }

                    if runner.isStarting {
                        HStack(spacing: 8) {
                            ProgressView()
                                .controlSize(.small)
                            Text(t(model.language, "正在启动抽卡脚本并连接设备，请稍候。", "Starting the draw script and connecting to the device."))
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(8)
            }

            if let currentSession {
                GroupBox(t(model.language, "当前抽卡状态", "Current Draw Status")) {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(spacing: 12) {
                            DrawMetricCard(
                                title: t(model.language, "抽卡次数", "Draw Count"),
                                value: "\(currentSession.drawStartedCount)",
                                detail: currentSession.updatedAtText
                            )
                            DrawMetricCard(
                                title: t(model.language, "目标命中", "Target Hits"),
                                value: "\(currentSession.targetHitCount)",
                                detail: currentSession.latestMatchedTemplate
                            )
                                    DrawMetricCard(
                                        title: t(model.language, "命中率", "Hit Rate"),
                                        value: currentSession.hitRateText,
                                        detail: t(model.language, "基于当前最新会话", "From Latest Session")
                                    )
                                    DrawMetricCard(
                                        title: t(model.language, "循环进度", "Cycle"),
                                        value: runner.progressText,
                                        detail: "\(t(model.language, "循环次数", "Cycles")) \(runner.cycleCountText)"
                                    )
                                }

                                ScrollView {
                                    Text(runner.logs.isEmpty ? t(model.language, "暂无抽卡日志", "No Draw Logs") : runner.logs)
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
                    .padding(8)
                }
            }

            if sessions.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text(t(model.language, "暂无抽卡记录", "No Draw Records"))
                        .font(.headline)
                    Text(t(model.language, "运行脚本并触发抽卡后，这里会显示历史记录、统计和前后截图。", "Run the draw script and trigger draws to see history, statistics, and before/after screenshots here."))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
            } else {
                HSplitView {
                    List(sessions, selection: $selectedSessionID) { session in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(session.sessionID)
                                .font(.system(size: 12, weight: .semibold, design: .monospaced))
                            Text(sessionSubtitle(session))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text("\(t(model.language, "抽卡", "Draws")) \(session.drawStartedCount)  ·  \(t(model.language, "命中", "Hits")) \(session.targetHitCount)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .tag(session.sessionID)
                    }
                    .frame(minWidth: 260, idealWidth: 300, maxWidth: 340)

                    ScrollView {
                        if let selectedSession {
                            VStack(alignment: .leading, spacing: 12) {
                                HStack(spacing: 12) {
                                    DrawMetricCard(
                                        title: t(model.language, "抽卡次数", "Draw Count"),
                                        value: "\(selectedSession.drawStartedCount)",
                                        detail: t(model.language, "当前会话", "Current Session")
                                    )
                                    DrawMetricCard(
                                        title: t(model.language, "目标命中", "Target Hits"),
                                        value: "\(selectedSession.targetHitCount)",
                                        detail: selectedSession.latestMatchedTemplate
                                    )
                                    DrawMetricCard(
                                        title: t(model.language, "命中率", "Hit Rate"),
                                        value: selectedSession.hitRateText,
                                        detail: t(model.language, "目标卡命中 / 抽卡次数", "Target Hits / Draws")
                                    )
                                    DrawMetricCard(
                                        title: t(model.language, "抽卡类型", "Draw Types"),
                                        value: "\(drawTypeSummary.max) / \(drawTypeSummary.min)",
                                        detail: t(model.language, "大抽 / 小抽", "Max / Min")
                                    )
                                }

                                GroupBox(t(model.language, "结果截图", "Result Screenshots")) {
                                    if pairs.isEmpty {
                                        Text(t(model.language, "当前会话还没有保存成对截图。", "No paired screenshots have been saved for this session yet."))
                                            .font(.footnote)
                                            .foregroundStyle(.secondary)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .padding(8)
                                    } else {
                                        HSplitView {
                                            List(pairs, selection: $selectedPairID) { pair in
                                                VStack(alignment: .leading, spacing: 4) {
                                                    Text(pairTitle(pair))
                                                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                                                    Text(pair.afterSavedAtText.isEmpty ? pair.beforeSavedAtText : pair.afterSavedAtText)
                                                        .font(.caption)
                                                        .foregroundStyle(.secondary)
                                                }
                                                .tag(pair.id)
                                            }
                                            .frame(minWidth: 200, idealWidth: 220, maxWidth: 260)

                                            if let selectedPair {
                                                VStack(alignment: .leading, spacing: 12) {
                                                    HStack {
                                                        Text(pairTitle(selectedPair))
                                                            .font(.headline)
                                                        Spacer()
                                                        Text(selectedPair.afterSavedAtText.isEmpty ? selectedPair.beforeSavedAtText : selectedPair.afterSavedAtText)
                                                            .font(.caption)
                                                            .foregroundStyle(.secondary)
                                                    }
                                                    HStack(alignment: .top, spacing: 12) {
                                                        DrawLocalImageView(
                                                            title: t(model.language, "抽卡前", "Before"),
                                                            url: selectedPair.beforeURL,
                                                            placeholder: t(model.language, "暂无抽卡前截图", "No Before Screenshot")
                                                        )
                                                        DrawLocalImageView(
                                                            title: t(model.language, "抽卡后", "After"),
                                                            url: selectedPair.afterURL,
                                                            placeholder: t(model.language, "暂无抽卡后截图", "No After Screenshot")
                                                        )
                                                    }
                                                }
                                                .padding(8)
                                            }
                                        }
                                        .frame(minHeight: 320)
                                    }
                                }

                                GroupBox(t(model.language, "事件时间线", "Event Timeline")) {
                                    if events.isEmpty {
                                        Text(t(model.language, "当前会话还没有事件记录。", "No events have been recorded for this session yet."))
                                            .font(.footnote)
                                            .foregroundStyle(.secondary)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .padding(8)
                                    } else {
                                        LazyVStack(alignment: .leading, spacing: 8) {
                                            ForEach(events) { event in
                                                VStack(alignment: .leading, spacing: 4) {
                                                    HStack {
                                                        Text(eventTitle(event.event))
                                                            .font(.system(size: 12, weight: .semibold))
                                                        if !event.drawType.isEmpty {
                                                            Text(event.drawType.uppercased())
                                                                .font(.caption.monospaced())
                                                                .foregroundStyle(.secondary)
                                                        }
                                                        Spacer()
                                                        Text(event.timestampText)
                                                            .font(.caption.monospaced())
                                                            .foregroundStyle(.secondary)
                                                    }
                                                    HStack {
                                                        Text("\(t(model.language, "抽卡", "Draws")) \(event.drawStartedCount)")
                                                            .font(.caption)
                                                            .foregroundStyle(.secondary)
                                                        Text("·")
                                                            .foregroundStyle(.secondary)
                                                        Text("\(t(model.language, "命中", "Hits")) \(event.targetHitCount)")
                                                            .font(.caption)
                                                            .foregroundStyle(.secondary)
                                                        if !event.matchedTemplate.isEmpty {
                                                            Text("·")
                                                                .foregroundStyle(.secondary)
                                                            Text(event.matchedTemplate)
                                                                .font(.caption.monospaced())
                                                                .foregroundStyle(.secondary)
                                                        }
                                                    }
                                                }
                                                .frame(maxWidth: .infinity, alignment: .leading)
                                                .padding(.vertical, 4)
                                                Divider()
                                            }
                                        }
                                        .padding(8)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        .onAppear {
            model.syncDrawRunnerDefaults()
            reload()
        }
        .onChange(of: selectedSessionID) { _ in
            loadSelectedSession()
        }
        .onReceive(refreshTimer) { _ in
            if runner.isRunning || runner.isStarting {
                reload()
            }
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox(t(model.language, "界面设置", "Interface")) {
                HStack {
                    Text(t(model.language, "语言", "Language"))
                    Picker("", selection: Binding(
                        get: { model.language },
                        set: { model.setLanguage($0) }
                    )) {
                        ForEach(AppLanguage.allCases) { lang in
                            Text(lang.displayName).tag(lang)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 120)
                    Spacer()
                }
                .padding(8)
            }
            GroupBox(t(model.language, "环境变量", "Environment Variables")) {
                ScriptEnvironmentSettingsView()
            }
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
            GroupBox(t(model.language, "录制诊断/自动修复", "Recording Diagnosis / Auto Fix")) {
                RecordingDiagnosticView(
                    diagnostic: model.diagnostic,
                    lang: model.language,
                    deviceOptions: model.availableDevices,
                    onDeviceChanged: model.rememberLastDevice(_:)
                )
            }
            Spacer()
        }
    }
}

struct ScriptEnvironmentSettingsView: View {
    @EnvironmentObject private var model: AppModel

    private func binding(for id: UUID, _ keyPath: WritableKeyPath<ScriptEnvironmentVariable, String>) -> Binding<String> {
        Binding(
            get: {
                model.scriptEnvironmentVariables.first(where: { $0.id == id })?[keyPath: keyPath] ?? ""
            },
            set: { newValue in
                model.updateScriptEnvironmentVariable(id: id, keyPath: keyPath, value: newValue)
            }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(
                t(
                    model.language,
                    "脚本里可以写 \"seconds\": \"${WAIT_SHORT}\" 或 \"remark\": \"等待${WAIT_SHORT}秒\"。如果整值就是变量，占位后会自动按数字/布尔/JSON 解析。",
                    "Use placeholders like \"seconds\": \"${WAIT_SHORT}\" or \"remark\": \"Wait ${WAIT_SHORT}s\". When the whole value is a variable, it is auto-parsed as number, bool, or JSON."
                )
            )
            .font(.footnote)
            .foregroundStyle(.secondary)
            .textSelection(.enabled)

            HStack(spacing: 8) {
                Text(t(model.language, "变量名", "Name"))
                    .frame(width: 160, alignment: .leading)
                Text(t(model.language, "值", "Value"))
                    .frame(minWidth: 180, alignment: .leading)
                Text(t(model.language, "备注", "Note"))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("")
                    .frame(width: 60)
            }
            .font(.footnote)
            .foregroundStyle(.secondary)

            if model.scriptEnvironmentVariables.isEmpty {
                Text(t(model.language, "暂无环境变量，点击下方按钮新增。", "No environment variables yet. Add one below."))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 4)
            }

            ForEach(model.scriptEnvironmentVariables) { item in
                HStack(alignment: .top, spacing: 8) {
                    TextField("WAIT_SHORT", text: binding(for: item.id, \.name))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 160)
                    TextField("0.5", text: binding(for: item.id, \.value))
                        .textFieldStyle(.roundedBorder)
                        .frame(minWidth: 180)
                    TextField(t(model.language, "例如：短等待秒数", "Example: short wait duration"), text: binding(for: item.id, \.note))
                        .textFieldStyle(.roundedBorder)
                    Button(t(model.language, "删除", "Delete")) {
                        model.removeScriptEnvironmentVariable(id: item.id)
                    }
                    .frame(width: 60)
                }
            }

            HStack {
                Button(t(model.language, "新增变量", "Add Variable")) {
                    model.addScriptEnvironmentVariable()
                }
                Spacer()
            }
        }
        .padding(8)
    }
}

struct RecordingDiagnosticView: View {
    @ObservedObject var diagnostic: RecordingDiagnosticModel
    let lang: AppLanguage
    let deviceOptions: [String]
    let onDeviceChanged: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(t(lang, "设备", "Device"))
                TextField("127.0.0.1:5555", text: $diagnostic.device)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: diagnostic.device) { newValue in
                        onDeviceChanged(newValue)
                    }
                Menu(t(lang, "选择", "Select")) {
                    Button(t(lang, "清空", "Clear")) { diagnostic.device = "" }
                    ForEach(deviceOptions, id: \.self) { serial in
                        Button(serial) {
                            diagnostic.device = serial
                            onDeviceChanged(serial)
                        }
                    }
                }
            }
            HStack {
                Button(diagnostic.isRunning ? t(lang, "诊断中...", "Diagnosing...") : t(lang, "开始诊断并自修复", "Run Diagnosis and Auto Fix")) {
                    diagnostic.start()
                }
                .disabled(diagnostic.isRunning)
                Button(t(lang, "停止诊断", "Stop Diagnosis")) {
                    diagnostic.stop()
                }
                .disabled(!diagnostic.isRunning)
                Button(t(lang, "清空日志", "Clear Logs")) {
                    diagnostic.clearLogs()
                }
            }
            if !diagnostic.lastApplied.isEmpty {
                Text("\(t(lang, "最近应用", "Last Applied")): \(diagnostic.lastApplied)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            if !diagnostic.lastReportPath.isEmpty {
                Text("\(t(lang, "报告路径", "Report Path")): \(diagnostic.lastReportPath)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            ScrollView {
                Text(diagnostic.logs.isEmpty ? t(lang, "暂无日志", "No logs") : diagnostic.logs)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(6)
                    .textSelection(.enabled)
            }
            .frame(height: 160)
            .background(Color(nsColor: .textBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.gray.opacity(0.4), lineWidth: 1)
            )
        }
        .padding(8)
    }
}

struct DeviceScreenshotCropperSheet: View {
    let capture: DeviceScreenshotCapture
    let lang: AppLanguage
    let onSaved: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var fileName: String
    @State private var selectionInImage: CGRect?
    @State private var errorMessage = ""

    init(capture: DeviceScreenshotCapture, lang: AppLanguage, onSaved: @escaping (String) -> Void) {
        self.capture = capture
        self.lang = lang
        self.onSaved = onSaved
        _fileName = State(initialValue: capture.defaultTemplateName)
    }

    private func aspectFitRect(imageSize: CGSize, containerSize: CGSize) -> CGRect {
        guard imageSize.width > 0, imageSize.height > 0, containerSize.width > 0, containerSize.height > 0 else {
            return .zero
        }
        let imageAspect = imageSize.width / imageSize.height
        let containerAspect = containerSize.width / containerSize.height
        if imageAspect > containerAspect {
            let width = containerSize.width
            let height = width / imageAspect
            return CGRect(x: 0, y: (containerSize.height - height) / 2.0, width: width, height: height)
        }
        let height = containerSize.height
        let width = height * imageAspect
        return CGRect(x: (containerSize.width - width) / 2.0, y: 0, width: width, height: height)
    }

    private func clampedPoint(_ point: CGPoint, to frame: CGRect) -> CGPoint {
        CGPoint(
            x: min(max(point.x, frame.minX), frame.maxX),
            y: min(max(point.y, frame.minY), frame.maxY)
        )
    }

    private func imagePoint(from viewPoint: CGPoint, imageFrame: CGRect) -> CGPoint {
        let safe = clampedPoint(viewPoint, to: imageFrame)
        let x = (safe.x - imageFrame.minX) / max(imageFrame.width, 1) * capture.pixelSize.width
        let y = (safe.y - imageFrame.minY) / max(imageFrame.height, 1) * capture.pixelSize.height
        return CGPoint(x: x, y: y)
    }

    private func viewRect(from imageRect: CGRect, imageFrame: CGRect) -> CGRect {
        CGRect(
            x: imageFrame.minX + imageRect.minX / max(capture.pixelSize.width, 1) * imageFrame.width,
            y: imageFrame.minY + imageRect.minY / max(capture.pixelSize.height, 1) * imageFrame.height,
            width: imageRect.width / max(capture.pixelSize.width, 1) * imageFrame.width,
            height: imageRect.height / max(capture.pixelSize.height, 1) * imageFrame.height
        )
    }

    private func normalizedRect(from a: CGPoint, to b: CGPoint) -> CGRect {
        CGRect(
            x: min(a.x, b.x),
            y: min(a.y, b.y),
            width: abs(a.x - b.x),
            height: abs(a.y - b.y)
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(t(lang, "从当前设备截图裁图", "Crop Template From Current Screenshot"))
                    .font(.headline)
                Spacer()
                Text("\(Int(capture.pixelSize.width)) x \(Int(capture.pixelSize.height))")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }

            HStack {
                Text(t(lang, "模板文件名", "Template File Name"))
                TextField("icon.png", text: $fileName)
                    .textFieldStyle(.roundedBorder)
                Button(t(lang, "打开图标目录", "Open Image Folder")) {
                    openImageTemplatesDirectoryInFinder()
                }
            }

            GeometryReader { geometry in
                let imageFrame = aspectFitRect(imageSize: capture.pixelSize, containerSize: geometry.size)
                ZStack(alignment: .topLeading) {
                    Color.black.opacity(0.06)
                    Image(nsImage: capture.image)
                        .resizable()
                        .interpolation(.high)
                        .aspectRatio(contentMode: .fit)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)

                    if let selectionInImage {
                        let rect = viewRect(from: selectionInImage, imageFrame: imageFrame)
                        Rectangle()
                            .stroke(Color.red, lineWidth: 2)
                            .frame(width: rect.width, height: rect.height)
                            .position(x: rect.midX, y: rect.midY)
                        Rectangle()
                            .fill(Color.red.opacity(0.15))
                            .frame(width: rect.width, height: rect.height)
                            .position(x: rect.midX, y: rect.midY)
                    }
                }
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            let start = imagePoint(from: value.startLocation, imageFrame: imageFrame)
                            let current = imagePoint(from: value.location, imageFrame: imageFrame)
                            selectionInImage = normalizedRect(from: start, to: current)
                            errorMessage = ""
                        }
                )
            }
            .frame(minHeight: 520)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.gray.opacity(0.35), lineWidth: 1)
            )

            Text(
                t(
                    lang,
                    "拖动鼠标框选图标区域。建议尽量只截取图标本体，少带背景，识别会更准。",
                    "Drag to select the icon region. A tight crop with minimal background usually matches best."
                )
            )
            .font(.footnote)
            .foregroundStyle(.secondary)

            if let selectionInImage {
                Text(
                    "x=\(Int(selectionInImage.minX)), y=\(Int(selectionInImage.minY)), w=\(Int(selectionInImage.width)), h=\(Int(selectionInImage.height))"
                )
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(.secondary)
            }

            if !errorMessage.isEmpty {
                Text(errorMessage)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }

            HStack {
                Button(t(lang, "取消", "Cancel")) {
                    dismiss()
                }
                Spacer()
                Button(t(lang, "保存模板", "Save Template")) {
                    guard let selectionInImage, selectionInImage.width >= 8, selectionInImage.height >= 8 else {
                        errorMessage = t(lang, "请先框选一个足够大的区域。", "Please select a sufficiently large region first.")
                        return
                    }
                    do {
                        let relativePath = try saveCroppedTemplateImage(
                            source: capture.cgImage,
                            cropRect: selectionInImage,
                            preferredName: fileName
                        )
                        onSaved(relativePath)
                        dismiss()
                    } catch {
                        errorMessage = error.localizedDescription
                    }
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(minWidth: 920, minHeight: 760)
    }
}

struct DeviceScreenshotRegionPickerSheet: View {
    let capture: DeviceScreenshotCapture
    let lang: AppLanguage
    let initialRegion: ImageSearchRegion?
    let onSaved: (ImageSearchRegion) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var selectionInImage: CGRect?
    @State private var errorMessage = ""

    private func aspectFitRect(imageSize: CGSize, containerSize: CGSize) -> CGRect {
        guard imageSize.width > 0, imageSize.height > 0, containerSize.width > 0, containerSize.height > 0 else {
            return .zero
        }
        let imageAspect = imageSize.width / imageSize.height
        let containerAspect = containerSize.width / containerSize.height
        if imageAspect > containerAspect {
            let width = containerSize.width
            let height = width / imageAspect
            return CGRect(x: 0, y: (containerSize.height - height) / 2.0, width: width, height: height)
        }
        let height = containerSize.height
        let width = height * imageAspect
        return CGRect(x: (containerSize.width - width) / 2.0, y: 0, width: width, height: height)
    }

    private func clampedPoint(_ point: CGPoint, to frame: CGRect) -> CGPoint {
        CGPoint(
            x: min(max(point.x, frame.minX), frame.maxX),
            y: min(max(point.y, frame.minY), frame.maxY)
        )
    }

    private func imagePoint(from viewPoint: CGPoint, imageFrame: CGRect) -> CGPoint {
        let safe = clampedPoint(viewPoint, to: imageFrame)
        let x = (safe.x - imageFrame.minX) / max(imageFrame.width, 1) * capture.pixelSize.width
        let y = (safe.y - imageFrame.minY) / max(imageFrame.height, 1) * capture.pixelSize.height
        return CGPoint(x: x, y: y)
    }

    private func viewRect(from imageRect: CGRect, imageFrame: CGRect) -> CGRect {
        CGRect(
            x: imageFrame.minX + imageRect.minX / max(capture.pixelSize.width, 1) * imageFrame.width,
            y: imageFrame.minY + imageRect.minY / max(capture.pixelSize.height, 1) * imageFrame.height,
            width: imageRect.width / max(capture.pixelSize.width, 1) * imageFrame.width,
            height: imageRect.height / max(capture.pixelSize.height, 1) * imageFrame.height
        )
    }

    private func normalizedRect(from a: CGPoint, to b: CGPoint) -> CGRect {
        CGRect(
            x: min(a.x, b.x),
            y: min(a.y, b.y),
            width: abs(a.x - b.x),
            height: abs(a.y - b.y)
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(t(lang, "选择图像识别区域", "Select Image Search Region"))
                    .font(.headline)
                Spacer()
                Text("\(Int(capture.pixelSize.width)) x \(Int(capture.pixelSize.height))")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }

            GeometryReader { geometry in
                let imageFrame = aspectFitRect(imageSize: capture.pixelSize, containerSize: geometry.size)
                ZStack(alignment: .topLeading) {
                    Color.black.opacity(0.06)
                    Image(nsImage: capture.image)
                        .resizable()
                        .interpolation(.high)
                        .aspectRatio(contentMode: .fit)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)

                    if let selectionInImage {
                        let rect = viewRect(from: selectionInImage, imageFrame: imageFrame)
                        Rectangle()
                            .stroke(Color.green, lineWidth: 2)
                            .frame(width: rect.width, height: rect.height)
                            .position(x: rect.midX, y: rect.midY)
                        Rectangle()
                            .fill(Color.green.opacity(0.15))
                            .frame(width: rect.width, height: rect.height)
                            .position(x: rect.midX, y: rect.midY)
                    }
                }
                .contentShape(Rectangle())
                .onAppear {
                    if let initialRegion {
                        selectionInImage = initialRegion.rect
                    }
                }
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            let start = imagePoint(from: value.startLocation, imageFrame: imageFrame)
                            let current = imagePoint(from: value.location, imageFrame: imageFrame)
                            selectionInImage = normalizedRect(from: start, to: current)
                            errorMessage = ""
                        }
                )
            }
            .frame(minHeight: 520)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.gray.opacity(0.35), lineWidth: 1)
            )

            Text(
                t(
                    lang,
                    "拖动鼠标框选要搜索的屏幕区域。区域越小，识别越快也越稳。",
                    "Drag to select the screen region where the icon should be searched. A smaller region is usually faster and more reliable."
                )
            )
            .font(.footnote)
            .foregroundStyle(.secondary)

            if let selectionInImage {
                Text(ImageSearchRegion(rect: selectionInImage).summaryText)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(.secondary)
            }

            if !errorMessage.isEmpty {
                Text(errorMessage)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }

            HStack {
                Button(t(lang, "取消", "Cancel")) {
                    dismiss()
                }
                Spacer()
                Button(t(lang, "确认区域", "Confirm Region")) {
                    guard let selectionInImage, selectionInImage.width >= 8, selectionInImage.height >= 8 else {
                        errorMessage = t(lang, "请先框选一个足够大的区域。", "Please select a sufficiently large region first.")
                        return
                    }
                    onSaved(ImageSearchRegion(rect: selectionInImage))
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(minWidth: 920, minHeight: 760)
    }
}

struct ScriptEditorView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var calibration: CalibrationModel
    @ObservedObject var debugRunner: RunnerModel
    @Binding var showNewScriptDialog: Bool
    @Binding var newScriptName: String
    let onNavigateToRun: () -> Void
    @State private var editorSelection = NSRange(location: 0, length: 0)
    @State private var editorViewIdentity = UUID()
    @State private var wheelCenterX = ""
    @State private var wheelBottomInset = "200"
    @State private var dragSeconds = "2"
    @State private var dragDistance = "180"
    @State private var dragAngle = "0"
    @State private var quickClickX = ""
    @State private var quickClickY = ""
    @State private var quickWaitSeconds = "1"
    @State private var quickFindText = "START"
    @State private var quickFindLang = "eng"
    @State private var quickFindTimeout = "8"
    @State private var quickImageTemplatePath = ""
    @State private var quickImageThreshold = "0.92"
    @State private var quickImageTimeout = "8"
    @State private var quickImagePreviewOnly = false
    @State private var quickImageRegion: ImageSearchRegion?
    @State private var quickImageRegionDisplayText = ""
    @State private var quickImageRegionDisplayIdentity = UUID()
    @State private var isCapturingImageTemplate = false
    @State private var imageTemplateCapture: DeviceScreenshotCapture?
    @State private var lastImageTemplateCapture: DeviceScreenshotCapture?
    @State private var imageTemplateWorkflowMessage = ""
    @State private var imageTemplateErrorMessage = ""
    @State private var showImageTemplateErrorAlert = false
    @State private var isCapturingImageRegion = false
    @State private var imageRegionCapture: DeviceScreenshotCapture?
    @State private var lastImageRegionCapture: DeviceScreenshotCapture?
    @State private var didSeedWheelDefaults = false
    @State private var showRunGuideAlert = false
    @State private var pendingInsertion: EditorInsertionRequest?

    private var latestPickedCoordinate: PickedCoordinate? {
        calibration.pickedCoordinates.first
    }

    private var currentDebugDeviceLabel: String {
        let device = model.preferredEditorDebugDevice().trimmingCharacters(in: .whitespacesAndNewlines)
        if device.isEmpty {
            return t(model.language, "未连接", "Not Connected")
        }
        return device
    }

    private var isDebugRunning: Bool {
        debugRunner.isRunning
    }

    private func editorDevice() -> String {
        let preferred = model.preferredDeviceForCurrentList(current: model.recorder.device)
        if !preferred.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return preferred
        }
        let candidates = [model.recorder.device, model.runnerA.device, model.runnerB.device, calibration.device]
        for item in candidates {
            let trimmed = item.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                return trimmed
            }
        }
        return ""
    }

    private func currentScreenSize() -> (width: Int, height: Int) {
        guard let adb = resolveADBExecutable(model.recorder.adbPath),
              let size = readScreenSize(adbPath: adb, device: editorDevice()) else {
            return (1080, 1920)
        }
        return size
    }

    private func seedWheelDefaultsIfNeeded() {
        guard !didSeedWheelDefaults else { return }
        applyDefaultWheelCenter()
        didSeedWheelDefaults = true
    }

    private func applyDefaultWheelCenter() {
        let size = currentScreenSize()
        wheelCenterX = "\(size.width / 2)"
    }

    private func wheelCenterY() -> Int {
        let size = currentScreenSize()
        let inset = Int(wheelBottomInset.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 200
        return max(0, size.height - inset)
    }

    private func insertTextAtCursor(_ insertion: String) {
        pendingInsertion = EditorInsertionRequest(text: insertion)
    }

    private func copyToPasteboard(_ value: String) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(value, forType: .string)
    }

    private func currentLineIndent() -> String {
        let nsText = model.editorText as NSString
        let safeLocation = min(max(0, editorSelection.location), nsText.length)
        let prefix = nsText.substring(to: safeLocation)
        let line = prefix.components(separatedBy: "\n").last ?? ""
        return String(line.prefix { $0 == " " || $0 == "\t" })
    }

    private func normalizedNumberText(_ value: Double) -> String {
        let text = String(format: "%.3f", value).replacingOccurrences(of: #"0+$"#, with: "", options: .regularExpression)
        let normalized = text.hasSuffix(".") ? String(text.dropLast()) : text
        return normalized.isEmpty ? "0" : normalized
    }

    private func normalizedAngleText(_ value: Double) -> String {
        let rounded = value.rounded()
        if abs(value - rounded) < 0.0001 {
            return String(Int(rounded))
        }
        return normalizedNumberText(value)
    }

    private func jsonEscaped(_ value: String) -> String {
        value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
    }

    private func makeWheelRemark(angleDegrees: Double, seconds: Double, prefix: String? = nil) -> String {
        let secondsText = normalizedNumberText(seconds)
        if let prefix, !prefix.isEmpty {
            return "\(prefix)\(secondsText)秒"
        }
        return "向\(normalizedAngleText(angleDegrees))度移动\(secondsText)秒"
    }

    private func makeWheelTraceSnippet(angleDegrees: Double, remark: String? = nil) -> String {
        let centerX = Int(wheelCenterX.trimmingCharacters(in: .whitespacesAndNewlines)) ?? (currentScreenSize().width / 2)
        let centerY = wheelCenterY()
        let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
        let durationMs = Int(seconds * 1000.0)
        let distance = max(1, Int(dragDistance.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 180)
        let radians = angleDegrees * Double.pi / 180.0
        let dx = cos(radians) * Double(distance)
        let dy = -sin(radians) * Double(distance)
        let targetX = centerX + Int(round(dx))
        let targetY = centerY + Int(round(dy))
        let indent = currentLineIndent()
        let bodyIndent = indent + "  "
        let remarkText = remark ?? makeWheelRemark(angleDegrees: angleDegrees, seconds: seconds)
        return """
\(indent){
\(bodyIndent)"type": "trace",
\(bodyIndent)"remark": "\(remarkText)",
\(bodyIndent)"points": [
\(bodyIndent)  {
\(bodyIndent)    "x": \(centerX),
\(bodyIndent)    "y": \(centerY),
\(bodyIndent)    "t_ms": 0
\(bodyIndent)  },
\(bodyIndent)  {
\(bodyIndent)    "x": \(targetX),
\(bodyIndent)    "y": \(targetY),
\(bodyIndent)    "t_ms": 100
\(bodyIndent)  },
\(bodyIndent)  {
\(bodyIndent)    "x": \(targetX),
\(bodyIndent)    "y": \(targetY),
\(bodyIndent)    "t_ms": \(durationMs)
\(bodyIndent)  }
\(bodyIndent)],
\(bodyIndent)"mode": "motion",
\(bodyIndent)"min_segment_ms": 1,
\(bodyIndent)"max_segment_ms": 1000
\(indent)},

"""
    }

    private func insertWheelTrace(angleDegrees: Double, remark: String? = nil) {
        insertTextAtCursor(makeWheelTraceSnippet(angleDegrees: angleDegrees, remark: remark))
    }

    private func makeClickSnippet(x: Int, y: Int) -> String {
        let indent = currentLineIndent()
        return """
\(indent){ "type": "click", "x": \(x), "y": \(y), "remark": "点击(\(x),\(y))" },

"""
    }

    private func makeWaitSnippet(seconds: Double) -> String {
        let indent = currentLineIndent()
        let normalized = normalizedNumberText(seconds)
        return """
\(indent){ "type": "wait", "seconds": \(normalized), "remark": "等待\(normalized)秒" },

"""
    }

    private func makeFindTextClickSnippet(text: String, lang: String, timeoutSeconds: Double) -> String {
        let indent = currentLineIndent()
        let normalizedTimeout = normalizedNumberText(timeoutSeconds)
        let safeText = jsonEscaped(text)
        let safeLang = jsonEscaped(lang)
        return """
\(indent){ "type": "find_text_click", "text": "\(safeText)", "match": "contains", "lang": "\(safeLang)", "timeout_sec": \(normalizedTimeout), "interval_sec": 0.8, "remark": "识别文字\(safeText)并点击" },

"""
    }

    private func makeFindImageClickSnippet(templatePath: String, threshold: Double, timeoutSeconds: Double, previewOnly: Bool) -> String {
        let indent = currentLineIndent()
        let normalizedThreshold = normalizedNumberText(threshold)
        let normalizedTimeout = normalizedNumberText(timeoutSeconds)
        let safePath = jsonEscaped(templatePath)
        let label = URL(fileURLWithPath: templatePath).lastPathComponent
        let safeLabel = jsonEscaped(label.isEmpty ? templatePath : label)
        let previewField = previewOnly ? #", "preview_only": true"# : ""
        let region = effectiveQuickImageRegion()
        return """
\(indent){ "type": "find_image_click", "template": "\(safePath)", "threshold": \(normalizedThreshold), "timeout_sec": \(normalizedTimeout), "interval_sec": 0.6, "region": { "x": \(region.x), "y": \(region.y), "width": \(region.width), "height": \(region.height) }\(previewField), "remark": "识别图标\(safeLabel)\(previewOnly ? "并高亮预览" : "并点击")" },

"""
    }

    private func makeFindImageSnippet(templatePath: String, threshold: Double, timeoutSeconds: Double) -> String {
        let indent = currentLineIndent()
        let normalizedThreshold = normalizedNumberText(threshold)
        let normalizedTimeout = normalizedNumberText(timeoutSeconds)
        let safePath = jsonEscaped(templatePath)
        let label = URL(fileURLWithPath: templatePath).lastPathComponent
        let safeLabel = jsonEscaped(label.isEmpty ? templatePath : label)
        let region = effectiveQuickImageRegion()
        return """
\(indent){ "type": "find_image", "template": "\(safePath)", "threshold": \(normalizedThreshold), "timeout_sec": \(normalizedTimeout), "interval_sec": 0.6, "region": { "x": \(region.x), "y": \(region.y), "width": \(region.width), "height": \(region.height) }, "remark": "识别图标\(safeLabel)" },

"""
    }

    private func makeIfImageSnippet(templatePath: String, threshold: Double, timeoutSeconds: Double) -> String {
        let indent = currentLineIndent()
        let normalizedThreshold = normalizedNumberText(threshold)
        let normalizedTimeout = normalizedNumberText(timeoutSeconds)
        let safePath = jsonEscaped(templatePath)
        let label = URL(fileURLWithPath: templatePath).lastPathComponent
        let safeLabel = jsonEscaped(label.isEmpty ? templatePath : label)
        let region = effectiveQuickImageRegion()
        let bodyIndent = indent + "  "
        let actionIndent = bodyIndent + "  "
        return """
\(indent){
\(bodyIndent)"type": "if_image",
\(bodyIndent)"template": "\(safePath)",
\(bodyIndent)"threshold": \(normalizedThreshold),
\(bodyIndent)"timeout_sec": \(normalizedTimeout),
\(bodyIndent)"interval_sec": 0.6,
\(bodyIndent)"region": { "x": \(region.x), "y": \(region.y), "width": \(region.width), "height": \(region.height) },
\(bodyIndent)"remark": "如果识别到图标\(safeLabel)",
\(bodyIndent)"then_actions": [
\(actionIndent){ "type": "click_match", "remark": "点击当前命中的图标" }
\(bodyIndent)],
\(bodyIndent)"else_actions": [
\(actionIndent){ "type": "wait", "seconds": 0.5, "remark": "未识别到图标后的分支" }
\(bodyIndent)]
\(indent)},

"""
    }

    private func effectiveQuickImageRegion() -> ImageSearchRegion {
        if let quickImageRegion {
            return quickImageRegion
        }
        let size = currentScreenSize()
        return ImageSearchRegion(x: 0, y: 0, width: size.width, height: size.height)
    }

    private func quickImageRegionSummaryText() -> String {
        if let quickImageRegion {
            return quickImageRegion.summaryText
        }
        let region = effectiveQuickImageRegion()
        return t(model.language, "全屏区域", "Full Screen Region") + " (\(region.summaryText))"
    }

    private func refreshQuickImageRegionDisplayText() {
        quickImageRegionDisplayText = quickImageRegionSummaryText()
        quickImageRegionDisplayIdentity = UUID()
    }

    private func imageTemplateInstructionText() -> String {
        if isCapturingImageTemplate {
            return t(
                model.language,
                "正在从当前设备抓取截图，请稍等。成功后会自动弹出裁图窗口。",
                "Capturing a screenshot from the current device. The cropper will open automatically when ready."
            )
        }
        if lastImageTemplateCapture != nil {
            return t(
                model.language,
                "截图已准备好。请在弹出的裁图窗口里拖拽框选图标；如果没看到弹窗，可以点“打开裁图窗口”。",
                "The screenshot is ready. Drag to select the icon in the cropper window. If you do not see it, click \"Open Cropper\"."
            )
        }
        return t(
            model.language,
            "推荐流程：1. 点“设备截图裁图” 2. 在裁图窗口框选图标 3. 点“保存模板” 4. 再插入 find_image_click。",
            "Recommended flow: 1. Click \"Crop From Device Screenshot\" 2. Select the icon in the cropper 3. Save the template 4. Insert find_image_click."
        )
    }

    private func importImageTemplate() {
        do {
            if let relativePath = try importImageTemplateWithOpenPanel() {
                quickImageTemplatePath = relativePath
                model.statusMessage = "已导入图标: \(relativePath)"
                imageTemplateWorkflowMessage = t(model.language, "已导入本地图标，可直接插入 find_image_click。", "Image imported. You can now insert find_image_click.")
            }
        } catch {
            model.statusMessage = "导入图标失败: \(error.localizedDescription)"
            imageTemplateErrorMessage = error.localizedDescription
            showImageTemplateErrorAlert = true
        }
    }

    private func chooseExistingImageTemplate() {
        do {
            if let relativePath = try chooseImageTemplateWithOpenPanel() {
                quickImageTemplatePath = relativePath
                model.statusMessage = "已选择图标: \(relativePath)"
                imageTemplateWorkflowMessage = t(model.language, "已选择图标，可直接插入 find_image_click。", "Image selected. You can now insert find_image_click.")
            }
        } catch {
            model.statusMessage = "选择图标失败: \(error.localizedDescription)"
            imageTemplateErrorMessage = error.localizedDescription
            showImageTemplateErrorAlert = true
        }
    }

    private func captureImageTemplateFromDevice() {
        let adbInput = model.recorder.adbPath
        let device = editorDevice()
        let suggestedPath = quickImageTemplatePath
        imageTemplateWorkflowMessage = t(model.language, "正在抓取当前设备截图...", "Capturing screenshot from current device...")
        imageTemplateErrorMessage = ""
        isCapturingImageTemplate = true
        DispatchQueue.global().async {
            do {
                let capture = try captureDeviceScreenshot(
                    adbPath: adbInput,
                    device: device,
                    suggestedTemplatePath: suggestedPath
                )
                DispatchQueue.main.async {
                    self.isCapturingImageTemplate = false
                    self.lastImageTemplateCapture = capture
                    self.imageTemplateCapture = capture
                    self.imageTemplateWorkflowMessage = t(
                        self.model.language,
                        "截图抓取成功，裁图窗口已打开。请框选目标图标后保存模板。",
                        "Screenshot captured successfully. The cropper is now open. Select the target icon and save the template."
                    )
                    self.model.statusMessage = "已抓取当前设备截图，请框选图标区域"
                }
            } catch {
                DispatchQueue.main.async {
                    self.isCapturingImageTemplate = false
                    self.model.statusMessage = "抓取设备截图失败: \(error.localizedDescription)"
                    self.imageTemplateWorkflowMessage = t(
                        self.model.language,
                        "设备截图失败。请确认模拟器窗口正常、设备已连接，然后重试。",
                        "Screenshot capture failed. Make sure the emulator is running and connected, then try again."
                    )
                    self.imageTemplateErrorMessage = error.localizedDescription
                    self.showImageTemplateErrorAlert = true
                }
            }
        }
    }

    private func reopenImageTemplateCropper() {
        guard let capture = lastImageTemplateCapture else { return }
        imageTemplateCapture = capture
        imageTemplateWorkflowMessage = t(
            model.language,
            "已重新打开裁图窗口，请框选图标后保存模板。",
            "Reopened the cropper. Select the icon and save the template."
        )
    }

    private func captureImageRegionFromDevice() {
        let adbInput = model.recorder.adbPath
        let device = editorDevice()
        isCapturingImageRegion = true
        imageTemplateErrorMessage = ""
        DispatchQueue.global().async {
            do {
                let capture = try captureDeviceScreenshot(
                    adbPath: adbInput,
                    device: device,
                    suggestedTemplatePath: "region.png"
                )
                DispatchQueue.main.async {
                    self.isCapturingImageRegion = false
                    self.lastImageRegionCapture = capture
                    self.imageRegionCapture = capture
                    self.model.statusMessage = "已抓取区域选择截图，请框选搜索区域"
                }
            } catch {
                DispatchQueue.main.async {
                    self.isCapturingImageRegion = false
                    self.model.statusMessage = "抓取区域截图失败: \(error.localizedDescription)"
                    self.imageTemplateErrorMessage = error.localizedDescription
                    self.showImageTemplateErrorAlert = true
                }
            }
        }
    }

    private func reopenImageRegionPicker() {
        guard let capture = lastImageRegionCapture else { return }
        imageRegionCapture = capture
        model.statusMessage = t(model.language, "已重新打开区域选择窗口。", "Reopened the region picker.")
    }

    private func resetQuickImageRegionToFullScreen() {
        quickImageRegion = nil
        refreshQuickImageRegionDisplayText()
        model.statusMessage = t(model.language, "已恢复为全屏搜索区域。", "Reset to full-screen search region.")
    }

    private func applySuggestedThresholdForRegionSelection() -> Bool {
        let trimmed = quickImageThreshold.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.isEmpty || trimmed == "0.92" else { return false }
        quickImageThreshold = "0.88"
        return true
    }

    private func startEditorCoordinatePicker() {
        let preferred = editorDevice()
        calibration.adbPath = model.recorder.adbPath
        calibration.device = preferred
        calibration.startCoordinatePicker(adbPath: calibration.adbPath, device: preferred)
    }

    private func stopEditorCoordinatePicker() {
        calibration.stopCoordinatePicker()
    }

    private func setWheelCenter(from picked: PickedCoordinate) {
        wheelCenterX = "\(picked.x)"
        let height = currentScreenSize().height
        wheelBottomInset = "\(max(0, height - picked.y))"
    }

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
                Button(isDebugRunning ? t(model.language, "停止调试", "Stop Debug") : t(model.language, "调试运行", "Debug Run")) {
                    if isDebugRunning {
                        debugRunner.stop()
                    } else {
                        model.startEditorDebugRun { ok in
                            if !ok {
                                showRunGuideAlert = true
                            }
                        }
                    }
                }
                Text("\(t(model.language, "当前设备", "Current Device")): \(currentDebugDeviceLabel)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            HStack {
                Picker(
                    t(model.language, "脚本", "Script"),
                    selection: Binding(
                        get: { model.selectedScriptName },
                        set: {
                            pendingInsertion = nil
                            editorSelection = NSRange(location: 0, length: 0)
                            model.selectScript(named: $0)
                            editorViewIdentity = UUID()
                        }
                    )
                ) {
                    ForEach(model.scripts.map(\.name), id: \.self) { name in
                        Text(name).tag(name)
                    }
                }
                Button(t(model.language, "打开目录", "Open")) {
                    openScriptsDirectoryInFinder()
                }
            }
            GroupBox(t(model.language, "取坐标", "Coordinate Picker")) {
        VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Button(t(model.language, "开始取点", "Start Picking")) {
                            startEditorCoordinatePicker()
                        }
                        .disabled(calibration.isPickingCoordinates)
                        Button(t(model.language, "停止取点", "Stop Picking")) {
                            stopEditorCoordinatePicker()
                        }
                        .disabled(!calibration.isPickingCoordinates)
                        Button(t(model.language, "清空坐标", "Clear Coordinates")) {
                            calibration.clearPickedCoordinates()
                        }
                        let statusText = calibration.isPickingCoordinates
                            ? t(model.language, "等待点击...", "Waiting for click...")
                            : t(model.language, "未监听", "Idle")
                        Text(statusText)
                            .foregroundStyle(calibration.isPickingCoordinates ? .orange : .secondary)
                    }
                    if let latestPickedCoordinate {
                        HStack {
                            Text("\(t(model.language, "最新坐标", "Latest")): x=\(latestPickedCoordinate.x), y=\(latestPickedCoordinate.y)")
                                .font(.system(size: 12, design: .monospaced))
                            Text(latestPickedCoordinate.device)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                        }
                    }
                    if !calibration.pickedCoordinates.isEmpty {
                        ScrollView {
                            VStack(alignment: .leading, spacing: 6) {
                                ForEach(calibration.pickedCoordinates) { picked in
                                    HStack {
                                        Text("x=\(picked.x), y=\(picked.y)")
                                            .font(.system(size: 12, design: .monospaced))
                                            .frame(width: 150, alignment: .leading)
                                        Text(picked.device)
                                            .font(.caption.monospaced())
                                            .foregroundStyle(.secondary)
                                            .frame(width: 120, alignment: .leading)
                                        Button(t(model.language, "插入 click", "Insert Click")) {
                                            insertTextAtCursor(makeClickSnippet(x: picked.x, y: picked.y))
                                        }
                                        Button(t(model.language, "插入坐标", "Insert Coordinates")) {
                                            insertTextAtCursor("\(picked.x), \(picked.y)")
                                        }
                                        Button(t(model.language, "复制", "Copy")) {
                                            copyToPasteboard("x=\(picked.x), y=\(picked.y)")
                                        }
                                        Button(t(model.language, "设为轮盘中心", "Set Wheel Center")) {
                                            setWheelCenter(from: picked)
                                        }
                                    }
                                }
                            }
                        }
                        .frame(height: 110)
                    }
                }
                .padding(8)
            }
            GroupBox(t(model.language, "快捷插入", "Quick Insert")) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("X")
                        TextField("540", text: $quickClickX)
                            .frame(width: 80)
                            .textFieldStyle(.roundedBorder)
                        Text("Y")
                        TextField("1680", text: $quickClickY)
                            .frame(width: 80)
                            .textFieldStyle(.roundedBorder)
                        Button(t(model.language, "插入 click", "Insert Click")) {
                            let x = Int(quickClickX.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
                            let y = Int(quickClickY.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
                            insertTextAtCursor(makeClickSnippet(x: x, y: y))
                        }
                        Spacer()
                        Text(t(model.language, "秒", "Seconds"))
                        TextField("1", text: $quickWaitSeconds)
                            .frame(width: 70)
                            .textFieldStyle(.roundedBorder)
                        Button(t(model.language, "插入 wait", "Insert Wait")) {
                            let seconds = Double(quickWaitSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 1
                            insertTextAtCursor(makeWaitSnippet(seconds: max(0, seconds)))
                        }
                    }
                    HStack {
                        Text(t(model.language, "文字", "Text"))
                        TextField("START", text: $quickFindText)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 180)
                        Text(t(model.language, "语言", "Lang"))
                        TextField("eng", text: $quickFindLang)
                            .frame(width: 80)
                            .textFieldStyle(.roundedBorder)
                        Text(t(model.language, "超时秒数", "Timeout"))
                        TextField("8", text: $quickFindTimeout)
                            .frame(width: 70)
                            .textFieldStyle(.roundedBorder)
                        Button(t(model.language, "插入 find_text_click", "Insert Find Text Click")) {
                            let targetText = quickFindText.trimmingCharacters(in: .whitespacesAndNewlines)
                            let lang = quickFindLang.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                ? "eng"
                                : quickFindLang.trimmingCharacters(in: .whitespacesAndNewlines)
                            let timeout = Double(quickFindTimeout.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 8
                            insertTextAtCursor(
                                makeFindTextClickSnippet(
                                    text: targetText.isEmpty ? "START" : targetText,
                                    lang: lang,
                                    timeoutSeconds: max(0.1, timeout)
                                )
                            )
                        }
                    }
                    HStack {
                        Text(t(model.language, "图标", "Image"))
                        TextField("../image_templates/icon.png", text: $quickImageTemplatePath)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 220)
                        Button(t(model.language, "上传图标", "Import Image")) {
                            importImageTemplate()
                        }
                        Button(t(model.language, "选择图标", "Select Image")) {
                            chooseExistingImageTemplate()
                        }
                        Button(
                            isCapturingImageTemplate
                                ? t(model.language, "截图中...", "Capturing...")
                                : t(model.language, "设备截图裁图", "Crop From Device Screenshot")
                        ) {
                            captureImageTemplateFromDevice()
                        }
                        .disabled(isCapturingImageTemplate)
                    }
                    HStack(spacing: 10) {
                        if isCapturingImageTemplate {
                            ProgressView()
                                .controlSize(.small)
                        }
                        Text(imageTemplateWorkflowMessage.isEmpty ? imageTemplateInstructionText() : imageTemplateWorkflowMessage)
                            .font(.footnote)
                            .foregroundStyle(isCapturingImageTemplate ? .orange : .secondary)
                        Spacer()
                        if lastImageTemplateCapture != nil && !isCapturingImageTemplate {
                            Button(t(model.language, "打开裁图窗口", "Open Cropper")) {
                                reopenImageTemplateCropper()
                            }
                        }
                    }
                    HStack(spacing: 10) {
                        Text(t(model.language, "区域", "Region"))
                        Text(quickImageRegionDisplayText.isEmpty ? quickImageRegionSummaryText() : quickImageRegionDisplayText)
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(.secondary)
                        Spacer()
                        if isCapturingImageRegion {
                            ProgressView()
                                .controlSize(.small)
                        }
                        Button(
                            isCapturingImageRegion
                                ? t(model.language, "区域截图中...", "Capturing Region...")
                                : t(model.language, "选择区域", "Select Region")
                        ) {
                            captureImageRegionFromDevice()
                        }
                        .disabled(isCapturingImageRegion)
                        if lastImageRegionCapture != nil && !isCapturingImageRegion {
                            Button(t(model.language, "打开区域窗口", "Open Region Picker")) {
                                reopenImageRegionPicker()
                            }
                        }
                        Button(t(model.language, "恢复全屏", "Reset Full Screen")) {
                            resetQuickImageRegionToFullScreen()
                        }
                    }
                    .id(quickImageRegionDisplayIdentity)
                    HStack {
                        Text(t(model.language, "阈值", "Threshold"))
                        TextField("0.92", text: $quickImageThreshold)
                            .frame(width: 70)
                            .textFieldStyle(.roundedBorder)
                        Text(t(model.language, "超时秒数", "Timeout"))
                        TextField("8", text: $quickImageTimeout)
                            .frame(width: 70)
                            .textFieldStyle(.roundedBorder)
                        Toggle(t(model.language, "仅预览不点击", "Preview Only"), isOn: $quickImagePreviewOnly)
                            .toggleStyle(.checkbox)
                        Button(t(model.language, "插入 find_image", "Insert Find Image")) {
                            let rawPath = quickImageTemplatePath.trimmingCharacters(in: .whitespacesAndNewlines)
                            let templatePath = rawPath.isEmpty ? "../image_templates/icon.png" : rawPath
                            let threshold = Double(quickImageThreshold.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0.92
                            let timeout = Double(quickImageTimeout.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 8
                            insertTextAtCursor(
                                makeFindImageSnippet(
                                    templatePath: templatePath,
                                    threshold: min(max(0.1, threshold), 1.0),
                                    timeoutSeconds: max(0.1, timeout)
                                )
                            )
                        }
                        Button(t(model.language, "插入 find_image_click", "Insert Find Image Click")) {
                            let rawPath = quickImageTemplatePath.trimmingCharacters(in: .whitespacesAndNewlines)
                            let templatePath = rawPath.isEmpty ? "../image_templates/icon.png" : rawPath
                            let threshold = Double(quickImageThreshold.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0.92
                            let timeout = Double(quickImageTimeout.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 8
                            insertTextAtCursor(
                                makeFindImageClickSnippet(
                                    templatePath: templatePath,
                                    threshold: min(max(0.1, threshold), 1.0),
                                    timeoutSeconds: max(0.1, timeout),
                                    previewOnly: quickImagePreviewOnly
                                )
                            )
                        }
                        Button(t(model.language, "插入 if_image", "Insert If Image")) {
                            let rawPath = quickImageTemplatePath.trimmingCharacters(in: .whitespacesAndNewlines)
                            let templatePath = rawPath.isEmpty ? "../image_templates/icon.png" : rawPath
                            let threshold = Double(quickImageThreshold.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0.92
                            let timeout = Double(quickImageTimeout.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 8
                            insertTextAtCursor(
                                makeIfImageSnippet(
                                    templatePath: templatePath,
                                    threshold: min(max(0.1, threshold), 1.0),
                                    timeoutSeconds: max(0.1, timeout)
                                )
                            )
                        }
                    }
                    HStack {
                        Text(t(model.language, "轮盘中心 X", "Wheel Center X"))
                        TextField("540", text: $wheelCenterX)
                            .frame(width: 80)
                            .textFieldStyle(.roundedBorder)
                        Text(t(model.language, "距离底部 Y", "Bottom Offset Y"))
                        TextField("200", text: $wheelBottomInset)
                            .frame(width: 80)
                            .textFieldStyle(.roundedBorder)
                        Button(t(model.language, "按分辨率默认", "Use Resolution Default")) {
                            applyDefaultWheelCenter()
                        }
                    }
                    HStack {
                        Text(t(model.language, "拖动秒数", "Drag Seconds"))
                        TextField("2", text: $dragSeconds)
                            .frame(width: 70)
                            .textFieldStyle(.roundedBorder)
                        Text(t(model.language, "拖动距离", "Drag Distance"))
                        TextField("180", text: $dragDistance)
                            .frame(width: 70)
                            .textFieldStyle(.roundedBorder)
                        Text(t(model.language, "角度", "Angle"))
                        TextField("0", text: $dragAngle)
                            .frame(width: 70)
                            .textFieldStyle(.roundedBorder)
                        Button(t(model.language, "插入角度拖动", "Insert Angle Drag")) {
                            let angle = Double(dragAngle.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
                            let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                            insertWheelTrace(angleDegrees: angle, remark: makeWheelRemark(angleDegrees: angle, seconds: seconds))
                        }
                    }
                    HStack {
                        Button(t(model.language, "向左拖动", "Drag Left")) {
                            let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                            insertWheelTrace(angleDegrees: 180, remark: makeWheelRemark(angleDegrees: 180, seconds: seconds, prefix: "向左移动"))
                        }
                        Button(t(model.language, "向上拖动", "Drag Up")) {
                            let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                            insertWheelTrace(angleDegrees: 90, remark: makeWheelRemark(angleDegrees: 90, seconds: seconds, prefix: "向上移动"))
                        }
                        Button(t(model.language, "向下拖动", "Drag Down")) {
                            let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                            insertWheelTrace(angleDegrees: -90, remark: makeWheelRemark(angleDegrees: -90, seconds: seconds, prefix: "向下移动"))
                        }
                        Button(t(model.language, "向右拖动", "Drag Right")) {
                            let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                            insertWheelTrace(angleDegrees: 0, remark: makeWheelRemark(angleDegrees: 0, seconds: seconds, prefix: "向右移动"))
                        }
                    }
                }
                .padding(8)
            }

            CodeTextView(text: $model.editorText, selectedRange: $editorSelection, pendingInsertion: $pendingInsertion)
                .id(editorViewIdentity)
                .border(Color.gray.opacity(0.4), width: 1)
                .onAppear {
                    seedWheelDefaultsIfNeeded()
                    refreshQuickImageRegionDisplayText()
                }

            Text(model.statusMessage)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .onChange(of: model.selectedScriptName) { newValue in
            editorViewIdentity = UUID()
        }
        .alert(t(model.language, "无法直接调试", "Unable to Debug Directly"), isPresented: $showRunGuideAlert) {
            Button(t(model.language, "前往运行", "Go to Run")) {
                onNavigateToRun()
            }
            Button(t(model.language, "取消", "Cancel"), role: .cancel) {}
        } message: {
            Text(t(model.language, "当前没有默认可连接设备。请先到运行模块确认设备，再执行调试运行。", "No default reachable device is available. Go to the Run section and confirm the device first."))
        }
        .alert(t(model.language, "设备截图失败", "Device Screenshot Failed"), isPresented: $showImageTemplateErrorAlert) {
            Button(t(model.language, "确定", "OK"), role: .cancel) {}
        } message: {
            Text(imageTemplateErrorMessage.isEmpty ? t(model.language, "请重试。", "Please try again.") : imageTemplateErrorMessage)
        }
        .sheet(item: $imageTemplateCapture) { capture in
            DeviceScreenshotCropperSheet(capture: capture, lang: model.language) { relativePath in
                quickImageTemplatePath = relativePath
                imageTemplateWorkflowMessage = t(
                    model.language,
                    "模板已保存，可直接插入 find_image_click。",
                    "Template saved. You can now insert find_image_click."
                )
                model.statusMessage = "已保存模板: \(relativePath)"
            }
        }
        .sheet(item: $imageRegionCapture) { capture in
            DeviceScreenshotRegionPickerSheet(
                capture: capture,
                lang: model.language,
                initialRegion: quickImageRegion
            ) { region in
                quickImageRegion = region
                refreshQuickImageRegionDisplayText()
                let thresholdAdjusted = applySuggestedThresholdForRegionSelection()
                model.statusMessage = thresholdAdjusted
                    ? "已设置图像识别区域: \(region.summaryText)，阈值已自动调整为 0.88"
                    : "已设置图像识别区域: \(region.summaryText)"
            }
        }
    }
}

struct CodeTextView: NSViewRepresentable {
    @Binding var text: String
    @Binding var selectedRange: NSRange
    @Binding var pendingInsertion: EditorInsertionRequest?

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: CodeTextView
        var isSyncing = false
        var lastAppliedInsertionID: EditorInsertionRequest.ID?

        init(parent: CodeTextView) {
            self.parent = parent
        }

        func textDidChange(_ notification: Notification) {
            guard !isSyncing,
                  let textView = notification.object as? NSTextView else { return }
            parent.text = textView.string
            parent.selectedRange = textView.selectedRange()
        }

        func textViewDidChangeSelection(_ notification: Notification) {
            guard !isSyncing,
                  let textView = notification.object as? NSTextView else { return }
            parent.selectedRange = textView.selectedRange()
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSScrollView()
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = true
        scrollView.autohidesScrollers = true
        scrollView.borderType = .noBorder

        let textView = NSTextView()
        textView.isRichText = false
        textView.isAutomaticQuoteSubstitutionEnabled = false
        textView.isAutomaticDashSubstitutionEnabled = false
        textView.isAutomaticTextReplacementEnabled = false
        textView.isContinuousSpellCheckingEnabled = false
        textView.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.delegate = context.coordinator
        textView.string = text
        textView.allowsUndo = true
        textView.isHorizontallyResizable = true
        textView.isVerticallyResizable = true
        textView.autoresizingMask = [.width]
        textView.textContainerInset = NSSize(width: 6, height: 6)
        textView.textContainer?.widthTracksTextView = false
        textView.textContainer?.containerSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)

        scrollView.documentView = textView
        return scrollView
    }

    private func clampedRange(_ range: NSRange, for textView: NSTextView) -> NSRange {
        let length = (textView.string as NSString).length
        let safeLocation = min(max(0, range.location), length)
        let safeLength = min(max(0, range.length), length - safeLocation)
        return NSRange(location: safeLocation, length: safeLength)
    }

    private func replaceTextExternally(_ textView: NSTextView, with newText: String) {
        let undoManager = textView.undoManager
        let shouldRestoreUndoRegistration = undoManager?.isUndoRegistrationEnabled ?? false
        if shouldRestoreUndoRegistration {
            undoManager?.disableUndoRegistration()
        }
        textView.string = newText
        if shouldRestoreUndoRegistration {
            undoManager?.enableUndoRegistration()
        }
        if let textStorage = textView.textStorage {
            undoManager?.removeAllActions(withTarget: textStorage)
        }
        undoManager?.removeAllActions(withTarget: textView)
    }

    private func applyInsertionIfNeeded(_ textView: NSTextView, context: Context) -> Bool {
        guard let request = pendingInsertion else { return false }
        if context.coordinator.lastAppliedInsertionID == request.id {
            return false
        }

        context.coordinator.lastAppliedInsertionID = request.id
        context.coordinator.isSyncing = true

        let safeRange = clampedRange(textView.selectedRange(), for: textView)

        textView.insertText(request.text, replacementRange: safeRange)
        let newRange = clampedRange(textView.selectedRange(), for: textView)
        textView.setSelectedRange(newRange)
        textView.scrollRangeToVisible(newRange)
        self.text = textView.string
        self.selectedRange = newRange

        context.coordinator.isSyncing = false
        DispatchQueue.main.async {
            if self.pendingInsertion?.id == request.id {
                self.pendingInsertion = nil
            }
        }
        return true
    }

    func updateNSView(_ nsView: NSScrollView, context: Context) {
        guard let textView = nsView.documentView as? NSTextView else { return }
        context.coordinator.parent = self
        if applyInsertionIfNeeded(textView, context: context) {
            return
        }
        context.coordinator.isSyncing = true
        if textView.string != text {
            replaceTextExternally(textView, with: text)
        }
        let safeSelectedRange = clampedRange(selectedRange, for: textView)
        if textView.selectedRange() != safeSelectedRange {
            textView.setSelectedRange(safeSelectedRange)
            textView.scrollRangeToVisible(safeSelectedRange)
        }
        context.coordinator.isSyncing = false
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
