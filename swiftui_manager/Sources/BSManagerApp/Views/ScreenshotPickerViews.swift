import SwiftUI
import AppKit

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
