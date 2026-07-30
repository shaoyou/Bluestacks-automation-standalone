# BS Manager Cross-Platform

This directory is an independent Electron + React + TypeScript implementation.
It does not modify or replace the existing SwiftUI application.

The Electron main process launches the existing Python automation scripts and
streams their output to the React UI through IPC. Packaged applications include
the Python source, plans, and image templates in their application resources;
the editable runtime copy lives in the app's user-data directory.

## Development prerequisites

- Android Platform Tools (`adb`) available on `PATH`, or configured in Settings.
- Python 3 available on `PATH`, or configured in Settings.
- Python dependencies required by the existing scripts:

```sh
python3 -m pip install numpy pillow
```

Windows development mode uses `adb.exe` and `python.exe` from Settings or `PATH`.
The packaged Windows application does not use these system dependencies.

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
Windows x64 machines and produces an NSIS installer and ZIP. Build the Windows
installer on Windows CI or a Windows host for the most reliable native
packaging and code-signing workflow.

`package:win7` produces a separate Windows 7 SP1 x64 legacy installer and ZIP.
It uses Electron 22.3.27, Platform Tools 34.0.4, and a Python 3.8-based backend; the normal package
continues to use the current Electron runtime for Windows 10/11. Do not
distribute the legacy package to Windows 10/11 users unless they specifically
need it: Electron 22 and Python 3.8 are end-of-life and receive no security
updates. A real Windows 7 SP1 x64 device remains required for final acceptance
testing. The target must have the current Visual C++ runtime and Windows updates
needed by the bundled Android Platform Tools.

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

### Self-contained Windows package

Build the Windows runtime on a Windows x64 machine or Windows CI before creating
the installer:

```powershell
cd electron_manager
powershell -ExecutionPolicy Bypass -File scripts/build_windows_runtime.ps1
./package_app.sh win
```

The script builds `adb_bot.exe` and `record_touch.exe` with PyInstaller, then
downloads Android Platform Tools and places all required files under
`vendor/windows/x64`. `package_app.sh win` refuses to create a Windows package
when any required runtime file is absent.

On first launch, the packaged Windows application copies the bundled runtime to
`%APPDATA%/bs-manager-cross-platform/runtime`, verifies `adb version`, displays
progress, and blocks automation pages until successful. The user can cancel and
retry preparation; cancellation leaves the environment unready and does not
require a system-wide Python or ADB installation.

### Windows 7 legacy package

Build the legacy runtime separately on Windows x64, then package it with the
legacy target:

```powershell
cd electron_manager
powershell -ExecutionPolicy Bypass -File scripts/build_windows_runtime.ps1 -Python python -Windows7Legacy -OutputDirectory vendor/windows/win7-x64
```

```sh
./package_app.sh win7
```

The output is written to `release/win7-legacy`. The GitHub Actions workflow
performs both builds and uploads two distinctly named artifacts.
