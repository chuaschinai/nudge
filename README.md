# Nudge

[![Build](https://github.com/chuaschinai/nudge/actions/workflows/release-windows.yml/badge.svg?branch=main)](https://github.com/chuaschinai/nudge/actions/workflows/release-windows.yml)
[![Release](https://img.shields.io/github/v/release/chuaschinai/nudge?label=release)](https://github.com/chuaschinai/nudge/releases/latest)

Nudge is an open-source application that turns a phone into a mouse and keyboard for a computer on the same local Wi-Fi network.

## Overview

- Server (PC): runs in Python, receives WebSocket messages, and controls the system mouse and keyboard.
- Client (phone): a responsive web page served by the computer; no installation is required on the phone.
- Access: the first connection from an IP address requires approval on the desktop app. Approved IP addresses are saved to a local whitelist and can reconnect without another prompt.

## Requirements

- Python 3.11+
- Access to a private local network
- Windows 10/11 or Linux with X11 (full input support)

## Installation

1. Clone the project.
2. Create a virtual environment (optional, but recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   .\.venv\Scripts\activate  # Windows PowerShell
   ```
3. Install the dependencies:
   ```bash
   python -m pip install aiohttp pynput pystray Pillow qrcode customtkinter
   ```
4. Start the server:
   ```bash
   python server/main.py
   ```

## Usage

1. On Windows, the server opens a desktop window with a QR code, address, connected devices, and a whitelist button. First-time connection requests appear in an approval dialog. Use **Exit** in the upper-left corner or **Quit** in the system tray to close the application.
2. On Linux, open the server URL printed in the terminal. Approve first-time connections in the terminal prompt.
3. On Windows, scan the QR code in the desktop window to open the client. On first connection, allow the request in the desktop app (or terminal on Linux). The client reconnects automatically after approval. Later connections from the same IP are approved automatically.
4. The desktop app's **Whitelist** button opens the allowed IP list. Use **Clear allowed IPs** to revoke all saved IP approvals and disconnect their active sessions.
5. Use the phone touchpad to move the cursor, click, drag, and scroll. A short movement cancels the press-and-hold gesture; keep your finger still to start dragging.
6. Use the Mouse, Keyboard, and Settings icons at the bottom. In Keyboard mode, the phone's virtual keyboard sends text incrementally to the desktop; Esc and F5 buttons are available beside the clipboard button. The client batches movement and sends deltas at up to about 120 times per second.
7. In Settings, adjust sensitivity and optionally enable the vertical scroll strip on the right edge of the touchpad. Two-finger scrolling and the side strip accumulate small movements to preserve slow scrolling.
8. The clipboard button attempts to send copied text from the phone. Over HTTP on a local network, browsers usually block direct clipboard access; in that case the button opens the mobile keyboard so its integrated clipboard can be used, without a Nudge popup. Settings replaces the touchpad and offers a disconnect action.

The desktop app displays the device type, browser, platform, and model when the browser provides that information. Device model availability depends on browser support and may be limited over local HTTP.

Inter and Iconoir are loaded from Google Fonts and jsDelivr, so an internet connection is needed for those fonts and icons; a local system font is used as a fallback. The controls and server communication continue to work over the local network.

## Configuration

The `config.json` file is created in the project root with these keys:

```json
{
  "port": 8765,
  "allowed_ips": [],
  "start_minimized": false,
  "autostart": false,
  "debug": false
}
```

- `port`: HTTP/WebSocket server port.
- `allowed_ips`: IP addresses that have been approved to reconnect without another prompt. This list can be managed from the desktop app.
- `start_minimized`: optional setting to start minimized.
- `autostart`: whether the application should start when the current user logs in. On Windows, the desktop checkbox updates the current user's Run registry entry. Startup launches the app minimized.
- `debug`: enables detailed console logs. It can also be enabled temporarily with `python server/main.py --debug`.
- The Windows desktop window uses CustomTkinter with a dark theme. Connected devices can be disconnected individually. The app version is shown beside the Nudge name.

Client files are separated by responsibility in `server/client/index.html`, `server/client/style.css`, and `server/client/app.js`.

Debug logs include connections, approvals, and received command types. Review network information before sharing logs.

## Security and limitations

- The application is designed for private local networks only.
- Do not expose the server to the internet; keep its port accessible only on the local network.
- Initial access requires manual approval. After approval, trust is based on the client's IP address; devices that share or are assigned that IP inherit its access until the whitelist is cleared.
- A change to a device's IP address requires approval again.
- Clearing the whitelist disconnects currently connected approved devices; they must be approved again before reconnecting.

### Windows

- In windows running as administrator, the PC accepts input only if the server also runs with administrator privileges.
- On first run, Windows may ask for permission to allow access to the private network.

### Linux

- Full input support depends on X11 through `pynput`.
- On Wayland, input injection is restricted and may not work correctly.
- Approval prompts are handled in the terminal. The server must have an interactive terminal to approve new connections.
- The system tray may not appear in GNOME environments without AppIndicator support; the application continues to run in the terminal and the local network.

## Packaging with PyInstaller

For a ready-to-run Windows build, download `Nudge.exe` from the latest release on the GitHub Releases page. The executable is built automatically when a version tag such as `v1.0.0` is pushed.

### Windows

```bash
python -m pip install aiohttp pynput pystray Pillow qrcode customtkinter pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name Nudge --collect-all customtkinter --collect-all pystray --collect-all pynput --add-data "server/client;server/client" --add-data "nudge-logo.svg;." server/main.py
```

The executable will be generated as `dist/Nudge.exe`. The packaged application stores its configuration and allowed-device list in `%APPDATA%\Nudge\config.json`.

### Linux

```bash
python -m pip install aiohttp pynput pystray Pillow qrcode customtkinter pyinstaller
pyinstaller --noconfirm --clean --onefile --collect-all customtkinter --collect-all pystray --collect-all pynput --add-data "server/client:server/client" --add-data "nudge-logo.svg:." --name Nudge server/main.py
```

To publish a Windows release, push a version tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

## Contributing

Contributions are welcome. Keep the implementation simple and focused on mouse, touchpad, keyboard, and access control.

## License

MIT
