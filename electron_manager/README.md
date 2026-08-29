# BS Manager Cross-Platform

This directory is an independent Electron + React + TypeScript implementation.
It does not modify or replace the existing SwiftUI application.

The Electron main process launches the existing Python automation scripts and
streams their output to the React UI through IPC. Packaged applications include
the Python source, plans, and image templates in their application resources.

## Development prerequisites

- Android Platform Tools (`adb`) available on `PATH`, or configured in Settings.
- Python 3 available on `PATH`, or configured in Settings.
- Python dependencies required by the existing scripts:

```sh
python3 -m pip install numpy pillow
```

Windows uses `adb.exe` and `python.exe` from Settings or `PATH` in both development and packaged builds.

## Development

```sh
cd electron_manager
npm install
npm run dev
```

## Packaging

```sh
npm run package:mac
npm run package:win
npm run package:win7
```

`package:mac` produces a macOS DMG and ZIP. `package:win` targets mainstream
Windows x64 machines and produces a Windows 10/11 NSIS installer and ZIP.
`package:win7` produces a separate Windows 7 SP1 x64 legacy NSIS installer and
ZIP.

For manual Windows packaging, run the commands in this order on a Windows host:

```sh
cd electron_manager
npm ci
npm run build
powershell -ExecutionPolicy Bypass -File scripts/build_windows_runtime.ps1 -Python python
npm run package:win
```

For the Windows 7 legacy package, use Python 3.8 and the legacy runtime output
directory:

```sh
powershell -ExecutionPolicy Bypass -File scripts/build_windows_runtime.ps1 -Python python -Windows7Legacy -OutputDirectory vendor/windows/win7-x64
npm run package:win7
```

`prepackage:win` and `prepackage:win7` only verify that the matching runtime is
already present.

## Professional edition activation

Free installs may run one automation task at a time. A Professional edition
license unlocks separate runner windows and defaults to three concurrent
runner tasks.

Before publishing the first Professional edition build, generate the signing
key once:

```sh
npm run license:keygen
```

This writes the compact-code Ed25519 private key to
`.local/license-ed25519-private-key.pem`, retains the legacy RSA key at
`.local/license-private-key.pem`, and updates `electron/license-public-key.ts`.
Keep both private keys outside version control and back them up securely. The
generated public keys are compiled into the application, so package a new build
after key generation.

For manual fulfillment, have the customer copy the installation ID from
Settings > Professional edition, then issue a code:

```sh
npm run license:issue -- \
  --install-id "customer-installation-id" \
  --expires 2027-07-30 \
  --max-runners 3
```

The default is the compact `P2-xxxxx-xxxxx-...` format, typically around 140
characters including grouping. The `--expires` option is optional for a
perpetual license. Send the resulting single-line activation code to the
customer. The app verifies its signature, installation ID, expiration date, and
runner limit locally before storing it.

Existing long RSA activation codes remain supported. Use
`--format legacy` only when a customer is using an older application build.

For the usual one-month manual license, use the local wrapper. It prompts for
the installation ID when no argument is provided, echoes the user ID, and
prints a compact activation code:

```sh
npm run license:issue:local
```

You can also supply the installation ID and optional concurrent-runner limit:

```sh
npm run license:issue:local -- "customer-installation-id" 3
```

### GitHub Releases 自动更新

现代 Windows 10/11 版和 macOS 版使用 GitHub Releases 作为更新源。
Windows 7 兼容版是单独的发布产物，不和现代版混装。应用启动后会自动检查更新；
用户可以在“设置”中手动检查、下载并重启安装。发布流程：

```sh
cd electron_manager
npm version patch --no-git-tag-version
git add package.json package-lock.json
git commit -m "release: v$(node -p "require('./package.json').version")"
git tag "v$(node -p "require('./package.json').version")"
git push origin main --tags
```

推送 `v*` 标签后，GitHub Actions 会构建 Windows 10/11 版、Windows 7 兼容版和
macOS 安装包，并自动创建 GitHub Release。Windows 10/11 版需要 Release 中包含
对应的 `latest.yml`；Windows 7 兼容版会单独输出到 `release/win7-legacy`。
如果要发布 macOS 更新，还需要把 `package:mac` 生成的 DMG 和 `latest-mac.yml`
一并上传到同一个 Release。

GitHub Actions 需要仓库允许 workflow 使用 `contents: write`，本项目工作流
已配置该权限。首次上线前应先用测试版本验证安装、下载、重启和回滚流程。
