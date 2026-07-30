import SwiftUI
import Foundation
import AppKit
import UniformTypeIdentifiers

func workspaceMarkerExists(at directory: URL) -> Bool {
    let fileManager = FileManager.default
    return fileManager.fileExists(atPath: directory.appendingPathComponent("plans", isDirectory: true).path)
        && fileManager.fileExists(atPath: directory.appendingPathComponent("adb_bot.py").path)
        && fileManager.fileExists(atPath: directory.appendingPathComponent("record_touch.py").path)
}

func synchronizedRuntimeRoot(from bundledRoot: URL) -> URL? {
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

func findAppRoot(startingAt directory: URL?) -> URL? {
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

func resolveAppRoot() -> URL {
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

let appRoot = resolveAppRoot()
let plansDir = appRoot.appendingPathComponent("plans", isDirectory: true)
let imageTemplatesDir = appRoot.appendingPathComponent("image_templates", isDirectory: true)
let recordingProfilesDir = appRoot.appendingPathComponent("recording_profiles", isDirectory: true)
let diagnosticsDir = appRoot.appendingPathComponent("diagnostics", isDirectory: true)
let drawStatsDir = diagnosticsDir.appendingPathComponent("draw_stats", isDirectory: true)
let drawResultPairsDir = diagnosticsDir.appendingPathComponent("draw_result_pairs", isDirectory: true)
let botScript = appRoot.appendingPathComponent("adb_bot.py")
let recorderScript = appRoot.appendingPathComponent("record_touch.py")
let appVersion = "1.1.0"
let mainWindowSceneID = "main-window"
let mainWindowIdentifier = NSUserInterfaceItemIdentifier(mainWindowSceneID)
let scriptEnvironmentReferenceRegex = try! NSRegularExpression(pattern: #"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)"#)

struct ScriptVariable: Identifiable, Codable, Equatable {
    var id: UUID = UUID()
    var name: String
    var value: String
    var note: String

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case value
        case note
    }

    init(id: UUID = UUID(), name: String, value: String, note: String) {
        self.id = id
        self.name = name
        self.value = value
        self.note = note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? container.decode(UUID.self, forKey: .id)) ?? UUID()
        name = (try? container.decode(String.self, forKey: .name)) ?? ""
        value = (try? container.decode(String.self, forKey: .value)) ?? ""
        note = (try? container.decode(String.self, forKey: .note)) ?? ""
    }
}

func scriptVariableDictionary(from variables: [ScriptVariable]) -> [String: String] {
    var resolved: [String: String] = [:]
    for item in variables {
        let name = item.name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { continue }
        resolved[name] = item.value
    }
    return resolved
}

func planScriptVariables(from rawPlan: [String: Any]) -> [ScriptVariable] {
    if let items = rawPlan["variables"] as? [[String: Any]] {
        return items.map { item in
            ScriptVariable(
                name: (item["name"] as? String) ?? "",
                value: stringValue(from: item["value"]),
                note: (item["note"] as? String) ?? ""
            )
        }
    }
    if let items = rawPlan["variables"] as? [String: Any] {
        return items
            .sorted { $0.key < $1.key }
            .map { key, value in
                ScriptVariable(name: key, value: stringValue(from: value), note: "")
            }
    }
    if let items = rawPlan["environment_variables"] as? [[String: Any]] {
        return items.map { item in
            ScriptVariable(
                name: (item["name"] as? String) ?? "",
                value: stringValue(from: item["value"]),
                note: (item["note"] as? String) ?? ""
            )
        }
    }
    return []
}

func loadScriptVariables(from scriptURL: URL) -> [ScriptVariable] {
    guard let data = try? Data(contentsOf: scriptURL),
          let rawPlan = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return []
    }
    return planScriptVariables(from: rawPlan)
}

func stringValue(from value: Any?) -> String {
    switch value {
    case let value as String:
        return value
    case let value as NSNumber:
        return value.stringValue
    case _ as NSNull:
        return ""
    case let value?:
        if JSONSerialization.isValidJSONObject([value]),
           let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
           let text = String(data: data, encoding: .utf8) {
            return text
        }
        return "\(value)"
    default:
        return ""
    }
}

func saveScriptVariables(_ variables: [ScriptVariable], to scriptURL: URL) throws {
    let data = try Data(contentsOf: scriptURL)
    var rawPlan = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    rawPlan["variables"] = variables.map { item in
        [
            "name": item.name,
            "value": item.value,
            "note": item.note,
        ]
    }
    rawPlan.removeValue(forKey: "environment_variables")
    let pretty = try JSONSerialization.data(withJSONObject: rawPlan, options: [.prettyPrinted, .sortedKeys])
    try pretty.write(to: scriptURL, options: [.atomic])
}

func scriptEnvironmentName(in text: String, match: NSTextCheckingResult) -> String? {
    if let range = Range(match.range(at: 1), in: text) {
        return String(text[range])
    }
    if let range = Range(match.range(at: 2), in: text) {
        return String(text[range])
    }
    return nil
}

func parseScriptEnvironmentLiteral(_ rawValue: String) -> Any {
    let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty, let data = trimmed.data(using: .utf8) else {
        return rawValue
    }
    if let parsed = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) {
        return parsed
    }
    return rawValue
}

func resolveScriptEnvironmentString(_ text: String, variables: [String: String], strict: Bool = false) -> Any {
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

func resolveScriptEnvironmentValue(_ value: Any, variables: [String: String], strict: Bool = false) -> Any {
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

func doubleValue(from rawValue: Any?, default defaultValue: Double = 0) -> Double {
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

func intValue(from rawValue: Any?, default defaultValue: Int = 0) -> Int {
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

func intDictionary(from rawValue: Any?) -> [String: Int] {
    guard let object = rawValue as? [String: Any] else { return [:] }
    return object.reduce(into: [String: Int]()) { result, item in
        let value = intValue(from: item.value)
        if value > 0 {
            result[item.key] = value
        }
    }
}

func stringDictionary(from rawValue: Any?) -> [String: String] {
    guard let object = rawValue as? [String: Any] else { return [:] }
    return object.reduce(into: [String: String]()) { result, item in
        let value = stringValue(from: item.value).trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.isEmpty {
            result[item.key] = value
        }
    }
}

func mergedEnvironment() -> [String: String] {
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

func expandTilde(_ path: String) -> String {
    (path as NSString).expandingTildeInPath
}

func isExecutable(_ path: String) -> Bool {
    FileManager.default.isExecutableFile(atPath: path)
}

func resolveADBExecutable(_ rawInput: String) -> String? {
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

func runADBCommand(adbPath: String, args: [String]) -> (code: Int32, text: String) {
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

func parseDeviceList(_ text: String) -> [String] {
    text
        .split(separator: "\n")
        .map(String.init)
        .filter { $0.contains("\tdevice") }
        .compactMap { $0.split(separator: "\t").first.map(String.init) }
}

func listConnectedDevices(adbPath: String) -> [String] {
    let first = runADBCommand(adbPath: adbPath, args: ["devices"])
    return parseDeviceList(first.text)
}

func adbOutputNeedsRecovery(code: Int32, text: String) -> Bool {
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

func recoverADBServer(adbPath: String) {
    _ = runADBCommand(adbPath: adbPath, args: ["kill-server"])
    _ = runADBCommand(adbPath: adbPath, args: ["start-server"])
}

func runADBShellCommandWithRecovery(adbPath: String, device: String, shellArgs: [String]) -> (code: Int32, text: String, recovered: Bool) {
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

func listConnectedDevicesWithRecovery(adbPath: String) -> (devices: [String], recovered: Bool) {
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

func readScreenSize(adbPath: String, device: String) -> (Int, Int)? {
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
        let matches = regex.matches(in: text, options: [], range: range)
        guard let match = matches.last,
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

func isADBDeviceReachable(adbPath: String, device: String) -> Bool {
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

func splitDevicesByReachability(adbPath: String, devices: [String]) -> (healthy: [String], unhealthy: [String]) {
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

func openScriptsDirectoryInFinder() {
    NSWorkspace.shared.open(plansDir)
}

func openImageTemplatesDirectoryInFinder() {
    try? FileManager.default.createDirectory(at: imageTemplatesDir, withIntermediateDirectories: true)
    NSWorkspace.shared.open(imageTemplatesDir)
}

func openDrawResultPairsDirectoryInFinder() {
    try? FileManager.default.createDirectory(at: drawResultPairsDir, withIntermediateDirectories: true)
    NSWorkspace.shared.open(drawResultPairsDir)
}

func imageTemplateReferencePath(for fileURL: URL) -> String {
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

func uniqueFileURL(in directory: URL, preferredName: String) -> URL {
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

func importImageTemplateWithOpenPanel() throws -> String? {
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

func chooseImageTemplateWithOpenPanel() throws -> String? {
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

func sanitizedDeviceFileName(_ device: String) -> String {
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

func recordingProfileURL(for device: String) -> URL {
    recordingProfilesDir.appendingPathComponent("\(sanitizedDeviceFileName(device)).json")
}

func diagnosticReportURL(for device: String) -> URL {
    diagnosticsDir.appendingPathComponent("\(sanitizedDeviceFileName(device))_diagnostic.json")
}

let runWindowScriptSeparator = ":::script:::"

func buildRunWindowValue(scriptName: String? = nil) -> String {
    let id = UUID().uuidString
    guard let scriptName, !scriptName.isEmpty else {
        return id
    }
    return "\(id)\(runWindowScriptSeparator)\(scriptName)"
}

func parseRunWindowValue(_ value: String) -> (windowID: String, scriptName: String?) {
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

func t(_ lang: AppLanguage, _ zh: String, _ en: String) -> String {
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

struct WheelTurnStep: Identifiable, Equatable {
    let id = UUID()
    var angle: String
    var seconds: String
}

let defaultRedRoleOrder = [
    "role_bosiwangzi.png",
    "role_kakaxi.png",
    "role_libai.png",
    "role_longsan.png",
    "role_lujuren.png",
    "role_shengqishi.png",
    "role_woailuo.png",
    "role_zhizhu.png",
]

let defaultRedRoleNotes = [
    "role_bosiwangzi.png": "波斯王子",
    "role_kakaxi.png": "卡卡西",
    "role_libai.png": "李白",
    "role_longsan.png": "龙三",
    "role_lujuren.png": "绿巨人",
    "role_shengqishi.png": "圣骑士",
    "role_woailuo.png": "我爱罗",
    "role_zhizhu.png": "蜘蛛",
]

struct DrawSessionSummary: Identifiable, Hashable {
    let id: String
    let sessionID: String
    let updatedAt: Date?
    let updatedAtText: String
    let drawStartedCount: Int
    let targetSeenCount: Int
    let targetHitCount: Int
    let latestEvent: String
    let latestDrawType: String
    let latestMatchedTemplate: String
    let latestMatchedRoleNote: String
    let roleHitCounts: [String: Int]
    let roleNotes: [String: String]
    let eventsURL: URL

    var hitRateText: String {
        guard drawStartedCount > 0 else { return "0%" }
        let value = Double(targetHitCount) / Double(drawStartedCount) * 100.0
        return String(format: "%.1f%%", value)
    }

    var targetSeenRateText: String {
        guard drawStartedCount > 0 else { return "0%" }
        let value = Double(targetSeenCount) / Double(drawStartedCount) * 100.0
        return String(format: "%.1f%%", value)
    }

    var targetHitRateText: String {
        hitRateText
    }

    var roleHitSummaryText: String {
        let items = roleHitCounts
            .filter { $0.value > 0 }
            .sorted {
                if $0.value != $1.value { return $0.value > $1.value }
                return displayName(for: $0.key) < displayName(for: $1.key)
            }
            .map { "\(displayName(for: $0.key))*\($0.value)" }
        return items.joined(separator: " · ")
    }

    var drawResultSummaryText: String {
        let defaultRoleIndex = Dictionary(
            uniqueKeysWithValues: defaultRedRoleOrder.enumerated().map { ($0.element, $0.offset) }
        )
        let templates = defaultRedRoleOrder + roleHitCounts.keys
            .filter { !defaultRedRoleOrder.contains($0) }
            .sorted()
        return templates
            .sorted {
                let lhsCount = roleHitCounts[$0] ?? 0
                let rhsCount = roleHitCounts[$1] ?? 0
                if lhsCount != rhsCount { return lhsCount > rhsCount }
                let lhsIndex = defaultRoleIndex[$0] ?? Int.max
                let rhsIndex = defaultRoleIndex[$1] ?? Int.max
                if lhsIndex != rhsIndex { return lhsIndex < rhsIndex }
                return displayName(for: $0) < displayName(for: $1)
            }
            .map { "\(displayName(for: $0))*\(roleHitCounts[$0] ?? 0)" }
            .joined(separator: " · ")
    }

    func displayName(for template: String) -> String {
        let fileName = URL(fileURLWithPath: template).lastPathComponent
        if let note = roleNotes[fileName], !note.isEmpty {
            return note
        }
        if let note = defaultRedRoleNotes[fileName] {
            return note
        }
        return fileName.isEmpty ? template : fileName
    }
}

struct DrawEventRecord: Identifiable, Hashable {
    let id: String
    let timestamp: Date?
    let timestampText: String
    let event: String
    let drawType: String
    let matchedTemplate: String
    let matchedRoleNote: String
    let drawStartedCount: Int
    let targetSeenCount: Int
    let targetHitCount: Int

    var matchedDisplayText: String {
        guard !matchedTemplate.isEmpty else { return "" }
        return matchedRoleNote.isEmpty ? matchedTemplate : "\(matchedRoleNote) (\(matchedTemplate))"
    }
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

struct AppOperationError: LocalizedError {
    let message: String

    var errorDescription: String? { message }
}

func sanitizedTemplateFileName(_ rawPath: String) -> String {
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

func captureDeviceScreenshot(adbPath: String, device: String, suggestedTemplatePath: String) throws -> DeviceScreenshotCapture {
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

func saveCroppedTemplateImage(source: CGImage, cropRect: CGRect, preferredName: String) throws -> String {
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

