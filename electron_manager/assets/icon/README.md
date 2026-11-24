# 应用图标

「熊熊乐园小助手」的小熊图标源文件与产物。

## 当前使用

深色版 **icon-dark.svg** 为正式图标：

- 深墨绿圆角底（`#24423B → #0B1214`），与 App 深色界面一致
- 蜂蜜奶油色小熊，粉色耳内与腮红、圆眼高光、"w" 嘴
- 头顶薄荷绿嫩芽，呼应品牌主色 `#8DE1C2`

薄荷版 **icon-mint.svg** 为备选备份（薄荷底 + 奶白小熊），未接入构建。

## 文件

| 文件 | 用途 |
| --- | --- |
| `icon.icns` | macOS 打包图标（package.json → build.mac.icon） |
| `icon.ico` | Windows 打包图标（package.json → build.win.icon） |
| `icon.png` | 1024×1024，开发模式 dock/任务栏图标 |
| `icon-src/` | SVG 源文件与预览图 |
| `icon-src/backup-mint/` | 薄荷版 icns/ico/1024 png 备份 |

## 重新生成

修改 `icon-src/icon-dark.svg` 后：

```bash
# 1. SVG → 1024 PNG（macOS 自带 Chrome）
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
  --default-background-color=00000000 --window-size=1024,1024 \
  --screenshot=icon.png "file://$PWD/icon-src/icon-dark.svg"

# 2. PNG → icns
mkdir -p /tmp/bear.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s icon.png --out /tmp/bear.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) icon.png --out /tmp/bear.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns /tmp/bear.iconset -o icon.icns

# 3. PNG → ico
python3 - <<'PY'
from PIL import Image
Image.open('icon.png').save('icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
PY
```

设计时始终在 16/32px 下检查可读性；圆形底与小熊脸部都留有约 14% 的边距，避免系统图标被裁切。
