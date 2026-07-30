import SwiftUI
import AppKit

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
        case "target_seen":
            return t(model.language, "目标出现", "Target Seen")
        case "target_hit":
            return t(model.language, "命中目标卡", "Target Hit")
        case "target_miss":
            return t(model.language, "未命中目标卡", "Target Miss")
        default:
            return event
        }
    }

    private func drawResultSummary(_ session: DrawSessionSummary) -> String {
        session.drawResultSummaryText
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
                        Picker("", selection: $runner.device) {
                            Text(t(model.language, "请选择设备", "Choose a Device"))
                                .tag("")
                            ForEach(model.availableDevices, id: \.self) { serial in
                                Text(serial).tag(serial)
                            }
                        }
                        .labelsHidden()
                        .frame(minWidth: 190)
                        .onChange(of: runner.device) { newValue in
                            model.rememberLastDevice(newValue)
                        }
                        TextField("emulator-5554", text: $runner.device)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 150)
                            .onChange(of: runner.device) { newValue in
                                model.rememberLastDevice(newValue)
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
                        Button(t(model.language, "复制日志", "Copy Logs")) {
                            runner.copyLogsToPasteboard()
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
                                title: t(model.language, "目标出现", "Target Seen"),
                                value: "\(currentSession.targetSeenCount)",
                                detail: currentSession.targetSeenRateText
                            )
                            DrawMetricCard(
                                title: t(model.language, "抽中次数", "Actual Hits"),
                                value: "\(currentSession.targetHitCount)",
                                detail: currentSession.targetHitRateText
                            )
                        }

                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(t(model.language, "抽卡结果", "Draw Results"))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(drawResultSummary(currentSession))
                                .font(.system(size: 13, weight: .semibold))
                                .lineLimit(3)
                            Spacer()
                        }
                        .padding(.horizontal, 8)
                        .padding(.vertical, 6)
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 6))

                        ScrollView {
                            Text(runner.logs.isEmpty ? t(model.language, "暂无抽卡日志", "No Draw Logs") : runner.logs)
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
                            Text(drawResultSummary(session))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
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
                                        detail: drawResultSummary(selectedSession)
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
                                                        Text("\(t(model.language, "出现", "Seen")) \(event.targetSeenCount)")
                                                            .font(.caption)
                                                            .foregroundStyle(.secondary)
                                                        Text("·")
                                                            .foregroundStyle(.secondary)
                                                        Text("\(t(model.language, "命中", "Hits")) \(event.targetHitCount)")
                                                            .font(.caption)
                                                            .foregroundStyle(.secondary)
                                                        if !event.matchedDisplayText.isEmpty {
                                                            Text("·")
                                                                .foregroundStyle(.secondary)
                                                            Text(event.matchedDisplayText)
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
        .onChange(of: model.availableDevices) { _ in
            runner.device = model.preferredDeviceForCurrentList(current: runner.device)
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
