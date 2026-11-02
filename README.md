# Ziggs Companion

A desktop companion for Albion Online focused on two local features:

- **Damage Meter**: captures Albion Photon packets and shows session damage by player, with each player's abilities in an expandable row. It can filter by party and by damage against players, and copy the ranking as text for the game chat.
- **Lootlog**: captures loot events, keeps the session locally, and exports a CSV in exactly the ao-loot-logger format, so the file works on any site that reads it.

The application also includes automatic updates, crash reports, system-tray behavior, and autostart. It does **not** provide tunneling, DNS optimization, historic battle or kill discovery, distributed scanning, market capture, or AODP delivery.

## Platforms

### Windows 10/11 (64-bit)

Packet capture works through one of two methods, chosen in **Settings**. Administrator rights follow the method: with Npcap the application opens without asking, and with WinDivert it reopens itself as administrator, which is when Windows asks. If you decline, the application stays open, explains what happened, and offers both ways out.

- **Npcap** (default on new installs): installed separately from [npcap.com](https://npcap.com/#download). Its free license does not allow redistribution, so it is not in the installer. Without it, the application opens normally, shows an install guide, and starts capturing by itself once Npcap is installed. The DLL is only loaded from `System32\Npcap`, and capture does not use promiscuous mode.
- **WinDivert** (included in the installer): for those who prefer not to install anything else, or who play with ExitLag, a VPN, or another route optimizer, where Npcap may not see the traffic. If Albion is open and Npcap receives no packets for a minute, the application suggests switching.

A configuration from before this choice stays on WinDivert: an update should not stop a capture that already works.

Starting with Windows also follows the method. WinDivert uses a scheduled task at the highest run level, which grants the rights at sign-in without Windows asking, and Npcap uses the user's `Run` key. The application reconciles these entries each time it opens.

### Linux x86_64

The interface can be built and used, but packet capture is not available.

## Development

From this repository's root:

```powershell
npm install
npm run tauri dev
```

To create a local build:

```powershell
npm run build
npm run tauri build
```

## Configuration

Local configuration is stored at:

- Windows: `%APPDATA%\ziggs-companion\config.json`
- Linux: `~/.config/ziggs-companion/config.json`
- macOS: `~/Library/Application Support/ziggs-companion/config.json`

The available options control Damage Meter, Lootlog, the capture method, autostart, and minimizing to the system tray. Capture controls are available in **Settings**; disabling a capture does not remove session data that was already collected.

### Catalogs and renders

The Companion keeps ability and item names in a local cache. If the backend is unavailable or returns an invalid response, local capture continues and the application retains the last valid catalog. Unnamed abilities retain a visible identifier, while unknown items use `IDX_{index}`. When artwork is unavailable, the name and a fixed-size placeholder remain visible.

### Window and scale

The window uses a fixed **1024 × 768 logical pixels**, without resizing, maximizing, or fullscreen. Operating-system scaling can change the physical pixel count, but not the application's logical area.

Use `Ctrl`/`Cmd` + `-` or `+` — including `=` and the equivalent numeric keypad keys — to adjust only the WebView scale from 80% to 150%. The preference is saved locally and never changes the native window size.

## Structure

```text
├── src/                    React/TypeScript interface
├── src-tauri/src/
│   ├── lib.rs              Tauri commands and application lifecycle
│   ├── sniffer.rs          Starts the chosen method and processes Photon packets
│   ├── npcap.rs            Capture through Npcap, with wpcap.dll loaded at runtime
│   ├── windivert.rs        Capture through WinDivert, included in the installer
│   ├── elevation.rs        Reopens as administrator when the method requires it
│   ├── autostart.rs        Starting with Windows, according to the method
│   ├── photon_parser.rs    Photon decoder and damage accumulator
│   ├── lootlog.rs          Item catalog, session, and CSV export
│   ├── crash_report.rs     Crash reports
│   └── api.rs              Ability/item catalogs and failure reporting
└── package.json            Development and build scripts
```

## Privacy

Damage Meter and Lootlog are processed locally. Loot CSV files are saved locally. The application only requests the catalogs it needs and may send pending crash reports.

## License

MIT. See [LICENSE](LICENSE).
