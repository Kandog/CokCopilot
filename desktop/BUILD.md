# Building the CokCopilot desktop app

The desktop app is a small Electron shell. It shows the web UI of the CokCopilot server it connects to. New server features (such as Groups) reach users without a new desktop build. Rebuild only when something in `desktop/` changes, or to ship a new version number.

## What you need

- Windows 10 or 11, x64. Windows builds must be made on Windows.
- [Node.js](https://nodejs.org) 18 or later: `winget install OpenJS.NodeJS.LTS`
- Internet access for the first build. electron-builder downloads Electron and its packaging tools.

## Build

1. Open a terminal in the `desktop` folder.

   ```bash
   cd desktop
   ```

2. Install the dependencies. You only need to do this the first time, or after `package.json` changes.

   ```bash
   npm install
   ```

3. Optional: run the app from source to try it.

   ```bash
   npm start
   ```

4. Build the release files.

   ```bash
   npm run dist
   ```

The files land in `desktop/release/`:

| File | What it is |
|---|---|
| `CokCopilot-<version>-portable.exe` | One file. Double-click to run, nothing to install. It unpacks itself to a temporary folder on every launch, so it starts a little slower. |
| `CokCopilot-<version>-win.zip` | The same app as a folder. Unzip it anywhere and run `CokCopilot.exe`. It starts faster. |
| `win-unpacked/` | The unzipped app, for testing. Don't distribute this folder. |

`npm run dist` builds both the portable `.exe` and the zip. To build only one of them, run:

```bash
npm run build && npx electron-builder --win portable --x64
```

## Set the version

The version comes from `version` in `desktop/package.json`, and it appears in the file names and in **Help → About**. Keep it the same as the server's version, which is in the repo's `VERSION` file, `backend/pyproject.toml` and `backend/cokcopilot/__init__.py`. To change it:

```bash
npm version 3.1.0 --no-git-tag-version
```

## Preset the server address

To send users straight to the sign-in screen, put a `cokcopilot.json` file next to the `.exe`, or inside the unzipped folder:

```json
{ "serverUrl": "https://chat.example.com" }
```

Without this file, the app asks for the address on first start. **File → Change server…** changes it later. Each user's choice is saved in `%APPDATA%\CokCopilot Desktop`.

## The "unknown publisher" warning

When you run the app, Windows may show **"Windows protected your PC"** with **Publisher: Unknown publisher**, or **"The publisher could not be verified"**. That's because:

1. **The app isn't code-signed.** The build log says `no signing info identified, signing is skipped`. With no digital signature, Windows can't tell who made the file.
2. **The file came from the internet, email or a network share.** Windows marks such files, and Microsoft Defender SmartScreen checks marked programs before they run. A new, unsigned program has no reputation, so SmartScreen warns.

On company PCs, IT policy often turns this warning into a block, or removes the **Run anyway** button. Policy may also allow only signed programs (AppLocker, WDAC or Smart App Control). The portable `.exe` unpacks and runs code from a temporary folder, which some security tools treat with extra suspicion.

### Fixes, from best to quickest

**1. Sign the app (recommended).** Signed apps show your organization's name instead of "Unknown publisher". Choose one way to sign:

- **Your company's own code-signing certificate.** Many IT departments have an internal certificate authority, and company PCs already trust it. Ask IT for a code-signing certificate as a `.pfx` file. Before building, set:

  In Command Prompt:

  ```bat
  set CSC_LINK=C:\path\to\codesign.pfx
  set CSC_KEY_PASSWORD=<the certificate password>
  npm run dist
  ```

  Keep the `.pfx` file and its password out of the repository.

  electron-builder then signs the `.exe` files automatically. This makes the app trusted on company PCs. It won't help on PCs outside the company.

- **A public code-signing certificate** from a certificate authority such as DigiCert or Sectigo, or **Azure Trusted Signing**. These work on any PC. Note that a new certificate still needs some downloads before SmartScreen stops warning, because reputation builds over time. See the electron-builder [Windows code-signing docs](https://www.electron.build/code-signing-win) for setup.

**2. Have IT deploy it.** If IT installs the app through Intune, SCCM or the company software portal, the file isn't marked as downloaded. IT can also allow the app by its file hash or publisher.

**3. Use the zip instead of the portable `.exe`.** The zip version doesn't unpack to a temporary folder, which some security tools flag. It's still unsigned, so this only helps with those tools, not with SmartScreen.

**4. Unblock it yourself, if policy allows.** This is only for your own PC, and only if you trust where the file came from. Right-click the file, choose **Properties**, tick **Unblock**, and click **OK**. Or click **More info → Run anyway** on the warning.

## Troubleshooting

- **"Cannot create symbolic link" during the build.** Turn on Windows **Developer Mode** (Settings → System → For developers), or run the build once as administrator. One of electron-builder's tool archives contains macOS symlinks.
- **The build can't download Electron behind a company proxy.** Set `HTTPS_PROXY` (and `ELECTRON_GET_USE_PROXY=1`) before running `npm install` and `npm run dist`.
- **macOS and Linux.** Build on those systems with `npm run dist:mac` or `npm run dist:linux`.
