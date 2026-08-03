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
  Npcap. Shows per-player breakdown by skill, timeline, and DPS. Players only
  (no mobs).
- **Lootlog capture**: Parses `/loot` chat output and generates CSV files
  compatible with ao-loot-logger. Optional auto-submit to guild events.

## Requirements

- Windows 10/11 (64-bit)
- [Npcap](https://npcap.com/) installed (for packet capture: damage meter,
  prices, market history). Without Npcap, the app runs but packet-dependent
  features are disabled. A download prompt is shown on first launch.
- Rust 1.77+ with MSVC build tools
- Node.js 18+
- For tunneling: a VPS with WireGuard support (see
  `docs/companion-vps-setup.sh`)

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

Route optimization requires a VPS running WireGuard near the Albion
datacenter. The included script (`docs/companion-vps-setup.sh`) provisions a
fresh Ubuntu/Debian VPS with:

- WireGuard server with split-tunneling (only Albion IPs allowed through)
- iptables rules with default DROP (non-Albion traffic is blocked)
- Cron job to re-resolve Albion IPs hourly (datacenters rotate)

Setup flow:
1. Generate a keypair in the companion (Tunnel tab)
2. Run the script on the VPS: `bash companion-vps-setup.sh <CLIENT_PUBKEY>`
3. Copy the server endpoint and public key back to the companion
4. The companion tests latency (direct vs tunnel) and only activates the
   tunnel if it improves routing

## Code signing

The build can sign Windows binaries via Microsoft Artifact Signing (Azure).
Without signing environment variables set, the build skips signing (binaries
work but may trigger SmartScreen warnings on Windows 11). See
`src-tauri/scripts/sign-windows.ps1` for details.

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
│   │   ├── sniffer.rs          Npcap packet capture, Photon event parsing
│   │   ├── photon_parser.rs    Photon protocol decoder, damage accumulator
│   │   ├── tunnel.rs           WireGuard tunnel (boringtun + wintun)
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
│   ├── scripts/
│   │   └── sign-windows.ps1    Code signing via Azure Artifact Signing
│   └── tauri.conf.json         Tauri config (bundle, updater, tray)
└── docs/
    └── companion-vps-setup.sh  VPS provisioning script
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