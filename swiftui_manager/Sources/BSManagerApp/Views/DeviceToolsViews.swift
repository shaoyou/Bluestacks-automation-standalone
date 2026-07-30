import SwiftUI

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
            HStack(spacing: 6) {
                Text(t(lang, "当前屏幕", "Current Screen"))
                Text(calibration.detectedScreenSize)
                    .font(.system(size: 12, design: .monospaced))
                Text(t(lang, "方案坐标会在运行时自动适配", "Plan coordinates are adapted automatically at runtime"))
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
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
