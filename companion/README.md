# Ziggs Companion

Aplicativo desktop para Albion Online focado em dois recursos locais:

- **Damage Meter**: captura pacotes Photon do Albion com WinDivert e mostra o dano da sessão por jogador.
- **Lootlog**: captura eventos de loot e exporta CSV compatível com ao-loot-logger.

O aplicativo também mantém atualização automática e relatórios de falha. Não executa túnel, otimização de DNS, escaneamento distribuído, captura de mercado ou envio ao AODP.

## Plataformas

### Windows 10/11 (64-bit)

A captura de pacotes requer privilégios de administrador e usa WinDivert, incluído no instalador.

### Linux x86_64

A interface pode ser compilada, mas a captura de pacotes via WinDivert não está disponível.

## Desenvolvimento

```powershell
cd companion
npm install
npm run tauri dev
```

```powershell
npm run build
npm run tauri build
```

### Build Linux assinada pelo Windows

O pacote Linux é compilado em um staging nativo do WSL 2, nunca em `/mnt/c`.
Instale uma vez no Ubuntu do WSL as dependências de build do Tauri:

```bash
sudo apt-get update && sudo apt-get install -y build-essential pkg-config libssl-dev \
  libgtk-3-dev libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev \
  libxdo-dev patchelf dpkg xdg-utils rsync file
```

Depois, execute na raiz do repositório pelo terminal Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\companion\scripts\build-linux.ps1
```

O script usa `Ubuntu` e o usuário WSL `gabriel` por padrão. Em outro ambiente,
informe ambos explicitamente:

```powershell
powershell -ExecutionPolicy Bypass -File .\companion\scripts\build-linux.ps1 -Distro Ubuntu -WslUser seu-usuario
```

A chave privada é lida diretamente de `%USERPROFILE%\.tauri\ziggs-companion.key`
pelo assinador Tauri dentro do WSL. Após a compilação, o script pede a senha com
entrada oculta diretamente no terminal WSL. A senha não é enviada ao PowerShell,
não aparece nos argumentos do processo Windows, não é salva em arquivo temporário
nem em variável persistente. Para a única chamada do assinador, ela existe
brevemente no ambiente do subprocesso Linux; execute a build em uma sessão WSL
local confiável. A build gera somente o `.deb`, sua assinatura `.deb.sig` e
`SHA256SUMS` em `~/artifacts/ziggs-companion/v<versão>/` no WSL. Ela não instala,
publica, cria release, altera o manifesto do updater nem acessa a VPS.

## Configuração

A configuração local fica em:

- Windows: `%APPDATA%\ziggs-companion\config.json`
- Linux: `~/.config/ziggs-companion/config.json`
- macOS: `~/Library/Application Support/ziggs-companion/config.json`

As opções disponíveis controlam o Damage Meter, o Lootlog, o início automático e a minimização para a bandeja. Os controles de captura ficam em **Configurações**; desligar uma captura não apaga a sessão já acumulada.

### Catálogos e renders

O Companion mantém em cache local os nomes de habilidades e itens. Se o backend estiver indisponível ou retornar uma resposta inválida, a captura local continua e o aplicativo mantém o último catálogo válido. Habilidades sem nome usam `Habilidade {id}` e itens desconhecidos usam `IDX_{índice}`; uma arte ausente mantém o nome e uma célula de tamanho fixo na interface.

### Janela e escala

A janela usa 1024 × 768 pixels lógicos, sem redimensionamento, maximização ou tela cheia. A escala do sistema operacional pode alterar os pixels físicos, mas não essa área lógica. Use `Ctrl`/`Cmd` + `-` ou `+` (inclusive `=` e teclas numéricas) para ajustar somente a escala da WebView entre 80% e 150%; a preferência fica localmente salva.

## Estrutura

```text
companion/
├── src/                    Interface React/TypeScript
├── src-tauri/src/
│   ├── lib.rs              Comandos Tauri e ciclo do aplicativo
│   ├── sniffer.rs          Captura WinDivert e processamento Photon
│   ├── photon_parser.rs    Decodificador Photon e acumulador de dano
│   ├── lootlog.rs          Catálogo de itens, sessão e exportação CSV
│   ├── crash_report.rs     Relatórios de falha
│   └── api.rs              Catálogos de habilidades/itens e falhas
└── scripts/publish.ps1     Publicação de artefatos assinados
```

## Privacidade

Damage Meter e Lootlog são processados localmente. O CSV de loot é salvo localmente. O aplicativo consulta somente os catálogos necessários e pode enviar relatórios de falha pendentes.

## Licença

MIT. Veja [LICENSE](LICENSE).
