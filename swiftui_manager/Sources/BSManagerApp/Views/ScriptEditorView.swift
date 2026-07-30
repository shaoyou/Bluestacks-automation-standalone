import SwiftUI
import AppKit

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
        debugRunner.isRunning || debugRunner.isStarting
    }

    private var debugButtonTitle: String {
        if debugRunner.isStarting {
            return t(model.language, "停止启动", "Cancel Debug")
        }
        if debugRunner.isRunning {
            return t(model.language, "停止调试", "Stop Debug")
        }
        return t(model.language, "调试运行", "Debug Run")
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

    private func wheelTargetPoint(centerX: Int, centerY: Int, distance: Int, angleDegrees: Double) -> (x: Int, y: Int) {
        let radians = angleDegrees * Double.pi / 180.0
        let dx = cos(radians) * Double(distance)
        let dy = -sin(radians) * Double(distance)
        return (centerX + Int(round(dx)), centerY + Int(round(dy)))
    }

    private func normalizedWheelTurnSteps() -> [(angle: Double, seconds: Double)] {
        model.editorWheelTurnSteps.compactMap { step in
            guard let angle = Double(step.angle.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                return nil
            }
            let seconds = max(0.1, Double(step.seconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0)
            return (angle, seconds)
        }
    }

    private func addWheelTurnStep(angleDegrees: Double? = nil, seconds: Double? = nil) {
        let angle = angleDegrees ?? (Double(dragAngle.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0)
        let duration = seconds ?? max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
        model.editorWheelTurnSteps.append(
            WheelTurnStep(
                angle: normalizedAngleText(angle),
                seconds: normalizedNumberText(duration)
            )
        )
        model.statusMessage = "已加入多转向步骤 \(model.editorWheelTurnSteps.count): \(makeWheelRemark(angleDegrees: angle, seconds: duration))"
    }

    private func updateWheelTurnStep(id: UUID, keyPath: WritableKeyPath<WheelTurnStep, String>, value: String) {
        guard let index = model.editorWheelTurnSteps.firstIndex(where: { $0.id == id }) else { return }
        model.editorWheelTurnSteps[index][keyPath: keyPath] = value
    }

    private func removeWheelTurnStep(id: UUID) {
        model.editorWheelTurnSteps.removeAll { $0.id == id }
    }

    private func makeMultiWheelTraceSnippet() -> String {
        let steps = normalizedWheelTurnSteps()
        guard !steps.isEmpty else {
            let angle = Double(dragAngle.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
            return makeWheelTraceSnippet(angleDegrees: angle, remark: nil)
        }

        let centerX = Int(wheelCenterX.trimmingCharacters(in: .whitespacesAndNewlines)) ?? (currentScreenSize().width / 2)
        let centerY = wheelCenterY()
        let distance = max(1, Int(dragDistance.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 180)
        let indent = currentLineIndent()
        let bodyIndent = indent + "  "
        let pointIndent = bodyIndent + "  "
        let fieldIndent = pointIndent + "  "

        var points: [(x: Int, y: Int, t: Int)] = [(centerX, centerY, 0)]
        var elapsedMs = 0
        for step in steps {
            let target = wheelTargetPoint(centerX: centerX, centerY: centerY, distance: distance, angleDegrees: step.angle)
            let durationMs = max(100, Int(step.seconds * 1000.0))
            let moveEndMs = min(elapsedMs + 100, elapsedMs + durationMs)
            points.append((target.x, target.y, moveEndMs))
            elapsedMs += durationMs
            if points.last?.t != elapsedMs {
                points.append((target.x, target.y, elapsedMs))
            }
        }

        let pointLines = points.enumerated().map { index, point in
            let comma = index == points.count - 1 ? "" : ","
            return """
\(pointIndent){
\(fieldIndent)"x": \(point.x),
\(fieldIndent)"y": \(point.y),
\(fieldIndent)"t_ms": \(point.t)
\(pointIndent)}\(comma)
"""
        }.joined(separator: "\n")
        let summary = steps.map { "向\(normalizedAngleText($0.angle))度\(normalizedNumberText($0.seconds))秒" }.joined(separator: " -> ")
        return """
\(indent){
\(bodyIndent)"type": "trace",
\(bodyIndent)"remark": "\(jsonEscaped(summary))",
\(bodyIndent)"points": [
\(pointLines)
\(bodyIndent)],
\(bodyIndent)"mode": "motion",
\(bodyIndent)"min_segment_ms": 1,
\(bodyIndent)"max_segment_ms": 1000
\(indent)},

"""
    }

    private func insertMultiWheelTrace() {
        insertTextAtCursor(makeMultiWheelTraceSnippet())
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
                    NSApp.keyWindow?.makeFirstResponder(nil)
                    model.saveCurrentScript()
                }
                Button(debugButtonTitle) {
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
                Text(t(model.language, "设备", "Device"))
                    .font(.footnote)
                Picker(
                    "",
                    selection: Binding(
                        get: { model.editorSelectedDevice },
                        set: { device in
                            model.editorSelectedDevice = device
                            model.rememberLastDevice(device)
                        }
                    )
                ) {
                    Text(t(model.language, "请选择设备", "Choose a Device"))
                        .tag("")
                    ForEach(model.availableDevices, id: \.self) { serial in
                        Text(serial).tag(serial)
                    }
                }
                .labelsHidden()
                .frame(minWidth: 180)
                Button {
                    model.refreshADBAndDevices(adbInput: model.recorder.adbPath)
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .help(t(model.language, "刷新设备", "Refresh Devices"))
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
            Text("\(t(model.language, "脚本目录", "Scripts Folder")): \(plansDir.path)")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
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
                        Button(t(model.language, "加入多转向列表", "Add to Multi-Turn")) {
                            addWheelTurnStep()
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
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("\(t(model.language, "多转向步骤", "Multi-Turn Steps")): \(model.editorWheelTurnSteps.count)")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            Button(t(model.language, "左", "Left")) {
                                let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                                addWheelTurnStep(angleDegrees: 180, seconds: seconds)
                            }
                            Button(t(model.language, "上", "Up")) {
                                let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                                addWheelTurnStep(angleDegrees: 90, seconds: seconds)
                            }
                            Button(t(model.language, "下", "Down")) {
                                let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                                addWheelTurnStep(angleDegrees: -90, seconds: seconds)
                            }
                            Button(t(model.language, "右", "Right")) {
                                let seconds = max(0.1, Double(dragSeconds.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 2.0)
                                addWheelTurnStep(angleDegrees: 0, seconds: seconds)
                            }
                            Button(t(model.language, "插入多转向", "Insert Multi-Turn")) {
                                insertMultiWheelTrace()
                                model.statusMessage = "已插入连续多转向 trace，共 \(model.editorWheelTurnSteps.count) 步"
                            }
                            .disabled(model.editorWheelTurnSteps.isEmpty)
                            Button(t(model.language, "清空步骤", "Clear Steps")) {
                                model.editorWheelTurnSteps.removeAll()
                            }
                            .disabled(model.editorWheelTurnSteps.isEmpty)
                        }
                        if model.editorWheelTurnSteps.isEmpty {
                            Text(t(model.language, "添加多个步骤后，会插入一条连续 trace；每步到达设定秒数后直接换方向。", "Add multiple steps to insert one continuous trace; each step changes direction after its duration."))
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(model.editorWheelTurnSteps) { step in
                                HStack(spacing: 8) {
                                    Text(t(model.language, "角度", "Angle"))
                                    TextField("0", text: Binding(
                                        get: { model.editorWheelTurnSteps.first(where: { $0.id == step.id })?.angle ?? "" },
                                        set: { updateWheelTurnStep(id: step.id, keyPath: \.angle, value: $0) }
                                    ))
                                    .frame(width: 70)
                                    .textFieldStyle(.roundedBorder)
                                    Text(t(model.language, "持续秒数", "Duration"))
                                    TextField("2", text: Binding(
                                        get: { model.editorWheelTurnSteps.first(where: { $0.id == step.id })?.seconds ?? "" },
                                        set: { updateWheelTurnStep(id: step.id, keyPath: \.seconds, value: $0) }
                                    ))
                                    .frame(width: 70)
                                    .textFieldStyle(.roundedBorder)
                                    Text("→ \(makeWheelRemark(angleDegrees: Double(step.angle) ?? 0, seconds: Double(step.seconds) ?? 0.1))")
                                        .font(.system(size: 12, design: .monospaced))
                                        .foregroundStyle(.secondary)
                                    Button(t(model.language, "删除", "Delete")) {
                                        removeWheelTurnStep(id: step.id)
                                    }
                                }
                            }
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
        .onChange(of: model.availableDevices) { _ in
            model.editorSelectedDevice = model.preferredDeviceForCurrentList(current: model.editorSelectedDevice)
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

        func textDidEndEditing(_ notification: Notification) {
            guard let textView = notification.object as? NSTextView else { return }
            parent.text = textView.string
            parent.selectedRange = textView.selectedRange()
        }

        func textViewDidChangeSelection(_ notification: Notification) {
            guard !isSyncing,
                  let textView = notification.object as? NSTextView else { return }
            guard !textView.hasMarkedText() else { return }
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
        guard !textView.hasMarkedText() else { return }
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
