# Ziggs Companion

Desktop application for Albion Online guilds using the Ziggs platform. Provides
distributed battle scanning, route optimization via WireGuard tunneling, DNS
testing, damage meter, and lootlog capture. Built with Tauri 2 (Rust core) and
React/TypeScript frontend.

## Features

- **Battle scanner**: Distributed scanning of Albion battle IDs across regions
  (Americas, Europe, Asia). Reports found/missing battles back to the Ziggs
  backend, which validates against the public Albion API.
- **Route optimization**: WireGuard tunnel to a VPS near the Albion datacenter,
  reducing latency. Split-tunneling: only Albion traffic goes through the
  tunnel. Automatic fallback to direct route if the VPS is unreachable.
- **DNS tester**: Pings Cloudflare, Google, Quad9, and OpenDNS to score the
  best resolver for the player's location.
- **Damage meter**: Captures damage events from Photon network packets via
  WinDivert. Shows per-player breakdown by skill, timeline, and DPS. Players only
  (no mobs).
- **Lootlog capture**: Parses `/loot` chat output and generates CSV files
  compatible with ao-loot-logger. Optional auto-submit to guild events.

## Requirements

- Windows 10/11 (64-bit)
- Administrator privileges (for WinDivert packet capture and wintun tunnel).
  No external driver installation needed — WinDivert DLL + .sys are bundled.
- Rust 1.77+ with MSVC build tools
- Node.js 18+

## Building from source

```sh
cd companion
npm install
npm run tauri dev    # development build with hot reload
npm run tauri build  # production build (outputs to src-tauri/target/release/bundle/)
```

The build requires the Rust toolchain, Windows SDK, and MSVC build tools. The
Tauri CLI handles frontend bundling automatically.

## Configuration

User configuration is stored at:
- Windows: `%APPDATA%\ziggs-companion\config.json`
- Linux: `~/.config/ziggs-companion/config.json`
- macOS: `~/Library/Application Support/ziggs-companion/config.json`

Key settings:
- `api_base_url`: Backend API URL (default: `http://localhost:8000`)
- `region`: Albion region (americas, europe, asia)
- `character_name`: Your in-game character name
- `tunnel_enabled`: Enable WireGuard route optimization
- `tunnel_endpoint`: VPS endpoint address
- `tunnel_server_pubkey`: VPS WireGuard public key
- `tunnel_client_privkey`: Client WireGuard private key
- `autostart`: Launch on system startup
- `minimize_to_tray`: Keep running in system tray when closed
- `collect_battles`, `collect_prices`, `collect_damage_meter`,
  `collect_auto_lootlog`: Toggle individual collectors

## Tunnel setup

Route optimization uses a WireGuard VPS near the Albion datacenter. The
companion auto-configures the endpoint and server public key based on the
Albion region detected from game traffic. The client private key is
generated automatically on first launch.

The companion tests latency (direct vs tunnel) before activating and falls
back to direct routing automatically if the VPS becomes unreachable.

## Code signing

The build can sign Windows binaries via Microsoft Artifact Signing (Azure).
Without signing environment variables set, the build skips signing (binaries
work but may trigger SmartScreen warnings on Windows 11).

## Project structure

```
companion/
├── src/                        React/TypeScript frontend
│   ├── App.tsx                 Main UI (tabs: Route, Damage, Lootlog)
│   ├── i18n.ts                 PT/EN/ES translations
│   └── styles.css              Global styles
├── src-tauri/                  Rust core
│   ├── src/
│   │   ├── lib.rs              Entry point, Tauri commands, background workers
│   │   ├── config.rs           CompanionConfig (JSON persistence)
│   │   ├── api.rs              HTTP client for backend API
│   │   ├── scanner.rs          Battle scan worker (claim/report cycle)
│   │   ├── sniffer.rs          WinDivert packet capture, Photon event parsing
│   │   ├── photon_parser.rs    Photon protocol decoder, damage accumulator
│   │   ├── tunnel.rs           WireGuard tunnel (boringtun + wintun)
│   │   ├── tunnel_presets.rs   WireGuard endpoints per Albion region
│   │   ├── dns.rs              DNS resolver tester
│   │   ├── lootlog.rs          /loot parser, CSV generator
│   │   ├── aodp.rs             Albion-Online-Data-Project feed (prices)
│   │   ├── albion_ips.rs       Resolve Albion hostnames to IPs (cached)
│   │   ├── albion_detect.rs    Detect running Albion process
│   │   ├── zone_detect.rs      Detect PvP zone from game state
│   │   ├── transfer.rs         Upload queue (prices, market history)
│   │   ├── persist.rs          Install ID persistence
│   │   ├── maps.rs             Map name lookup
│   │   └── winutil.rs          Windows admin check
│   ├── resources/
│   │   └── wintun.dll          Windows virtual network driver
│   └── tauri.conf.json         Tauri config (bundle, updater, tray)
```

## Privacy

The companion does not collect or transmit personal data. Packet capture is
processed locally. Battle scan and price data are submitted to the Ziggs
backend, which validates everything against the public Albion API. No
telemetry, no analytics, no tracking.

Discord login is optional and only used for lootlog auto-submit. The
companion stores a bearer token (30-day validity) in the local config file.
No Discord credentials are stored.

## License

MIT. See [LICENSE](LICENSE).