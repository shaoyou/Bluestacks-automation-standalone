import SwiftUI
import AppKit

struct RunnerView: View {
    @ObservedObject var runner: RunnerModel
    let lang: AppLanguage
    let scripts: [String]
    let deviceOptions: [String]
    let refreshScripts: () -> Void
    let refreshDevices: (String) -> Void
    let onDeviceChanged: (String) -> Void
    let resolveScriptURL: (String) -> URL?
    let onSelectionChanged: (String, String) -> Void
    let openAdditionalRun: (() -> Void)?

    private var currentScriptURL: URL? {
        resolveScriptURL(runner.selectedScript)
    }

    private var isRunActive: Bool {
        runner.isRunning || runner.isStarting
    }

    private var runButtonTitle: String {
        if runner.isStarting {
            return t(lang, "停止启动 \(runner.slotName)", "Cancel \(runner.slotName)")
        }
        if runner.isRunning {
            return t(lang, "停止 \(runner.slotName)", "Stop \(runner.slotName)")
        }
        return t(lang, "运行 \(runner.slotName)", "Run \(runner.slotName)")
    }

    private var runButtonIcon: String {
        isRunActive ? "stop.fill" : "play.fill"
    }

    private func variableBinding(for id: UUID, _ keyPath: WritableKeyPath<ScriptVariable, String>) -> Binding<String> {
        Binding(
            get: {
                runner.scriptVariables.first(where: { $0.id == id })?[keyPath: keyPath] ?? ""
            },
            set: { newValue in
                runner.updateScriptVariable(id: id, keyPath: keyPath, value: newValue, scriptURL: currentScriptURL)
            }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            GroupBox {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .center, spacing: 10) {
                        Text(t(lang, "脚本", "Script"))
                            .frame(width: 36, alignment: .trailing)
                        Picker("", selection: $runner.selectedScript) {
                            ForEach(scripts, id: \.self) { name in
                                Text(name).tag(name)
                            }
                        }
                        .labelsHidden()
                        .frame(minWidth: 220)
                        .onChange(of: runner.selectedScript) { newValue in
                            onSelectionChanged(runner.slotName, newValue)
                            runner.reloadScriptVariables(scriptURL: resolveScriptURL(newValue))
                        }
                        Button {
                            refreshScripts()
                        } label: {
                            Label(t(lang, "刷新", "Refresh"), systemImage: "arrow.clockwise")
                        }

                        Button {
                            openScriptsDirectoryInFinder()
                        } label: {
                            Label(t(lang, "打开目录", "Open Folder"), systemImage: "folder")
                        }

                        Divider()
                            .frame(height: 18)

                        Text(t(lang, "设备", "Device"))
                            .frame(width: 36, alignment: .trailing)
                        TextField("127.0.0.1:5555", text: $runner.device)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 160)
                            .onChange(of: runner.device) { newValue in
                                onDeviceChanged(newValue)
                            }
                        Menu {
                            Button(t(lang, "清空", "Clear")) { runner.device = "" }
                            ForEach(deviceOptions, id: \.self) { serial in
                                Button(serial) {
                                    runner.device = serial
                                    onDeviceChanged(serial)
                                }
                            }
                        } label: {
                            Label(t(lang, "选择", "Select"), systemImage: "list.bullet")
                        }
                        Button {
                            refreshDevices(runner.adbPath)
                        } label: {
                            Label(t(lang, "刷新", "Refresh"), systemImage: "arrow.clockwise")
                        }
                    }

                    HStack(alignment: .center, spacing: 10) {
                        Button {
                            if isRunActive {
                                runner.stop()
                            } else {
                                runner.start(scriptURL: currentScriptURL)
                            }
                        } label: {
                            Label(runButtonTitle, systemImage: runButtonIcon)
                                .frame(minWidth: 110)
                        }
                        .keyboardShortcut(.defaultAction)
                        .disabled(!isRunActive && currentScriptURL == nil)

                        if let openAdditionalRun {
                            Button {
                                openAdditionalRun()
                            } label: {
                                Label(t(lang, "多开运行", "Open Additional"), systemImage: "plus.square.on.square")
                            }
                        }

                        Button {
                            runner.clearLogs()
                        } label: {
                            Label(t(lang, "清空日志", "Clear Logs"), systemImage: "trash")
                        }
                        Button {
                            runner.copyLogsToPasteboard()
                        } label: {
                            Label(t(lang, "复制日志", "Copy Logs"), systemImage: "doc.on.doc")
                        }

                        Toggle(
                            t(lang, "实时输出", "Realtime Output"),
                            isOn: $runner.showRealtimeCommandLogs
                        )
                        .toggleStyle(.checkbox)

                        Divider()
                            .frame(height: 18)

                        Text("\(t(lang, "进度", "Progress")): \(runner.progressText)")
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(.secondary)
                        Text("\(t(lang, "循环", "Loops")): \(runner.cycleCountText)")
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(.secondary)

                        Spacer()

                        Text(t(lang, "单次收益", "Profit"))
                            .foregroundStyle(.secondary)
                        TextField("0", text: $runner.profitPerCycle)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 92)
                        Text("\(t(lang, "预期", "Expected")): \(runner.expectedProfitText)")
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(4)
            }
            ScriptVariablesRunPanel(
                lang: lang,
                variables: runner.scriptVariables,
                statusMessage: runner.variableStatusMessage,
                valueBinding: { id in variableBinding(for: id, \.value) },
                noteBinding: { id in variableBinding(for: id, \.note) }
            )
            ScrollView {
                Text(runner.logs.isEmpty ? t(lang, "暂无日志", "No logs") : runner.logs)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(6)
            }
            .frame(height: 180)
            .background(Color(nsColor: .textBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.gray.opacity(0.4), lineWidth: 1)
            )
        }
        .padding(10)
        .onAppear {
            runner.reloadScriptVariables(scriptURL: currentScriptURL)
        }
    }
}

struct ScriptVariablesRunPanel: View {
    let lang: AppLanguage
    let variables: [ScriptVariable]
    let statusMessage: String
    let valueBinding: (UUID) -> Binding<String>
    let noteBinding: (UUID) -> Binding<String>

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(t(lang, "变量名", "Variable"))
                    .frame(width: 150, alignment: .leading)
                Text(t(lang, "值", "Value"))
                    .frame(width: 180, alignment: .leading)
                Text(t(lang, "备注", "Note"))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .font(.footnote)
            .foregroundStyle(.secondary)

            if variables.isEmpty {
                Text(t(lang, "当前脚本没有 variables。可在脚本根级添加 variables 后在这里编辑值和备注。", "This script has no variables. Add a root-level variables list to edit values and notes here."))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(variables) { item in
                    HStack(alignment: .top, spacing: 8) {
                        Text(item.name.isEmpty ? t(lang, "未命名", "Unnamed") : item.name)
                            .font(.system(size: 12, design: .monospaced))
                            .frame(width: 150, alignment: .leading)
                            .padding(.top, 5)
                            .textSelection(.enabled)
                        TextField("0.5", text: valueBinding(item.id))
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 180)
                        TextField(t(lang, "备注", "Note"), text: noteBinding(item.id))
                            .textFieldStyle(.roundedBorder)
                    }
                }
            }

            if !statusMessage.isEmpty {
                Text(statusMessage)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .padding(8)
        .background(Color(nsColor: .controlBackgroundColor))
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .stroke(Color.gray.opacity(0.25), lineWidth: 1)
        )
    }
}
