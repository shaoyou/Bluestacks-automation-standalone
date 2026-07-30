import SwiftUI

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
