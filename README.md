# Ziggs Companion

A desktop companion for Albion Online focused on two local features:

- **Damage Meter**: captures Albion Photon packets through WinDivert and shows session damage by player and ability.
- **Lootlog**: captures loot events, keeps the session locally, and exports CSV compatible with ao-loot-logger.

The application also includes automatic updates, crash reports, system-tray behavior, and autostart. It does **not** provide tunneling, DNS optimization, historic battle or kill discovery, distributed scanning, market capture, or AODP delivery.

## Platforms

### Windows 10/11 (64-bit)

Packet capture requires administrator privileges and uses WinDivert, which is included in the installer.

### Linux x86_64

The interface can be built and used, but packet capture through WinDivert is not available.

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

The available options control Damage Meter, Lootlog, autostart, and minimizing to the system tray. Capture controls are available in **Settings**; disabling a capture does not remove session data that was already collected.

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
│   ├── sniffer.rs          WinDivert capture and Photon processing
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
