# Ziggs Companion

Aplicativo desktop para Albion Online focado em dois recursos locais:

- **Damage Meter**: captura pacotes Photon do Albion com WinDivert e mostra o dano da sessão por jogador e habilidade.
- **Lootlog**: captura eventos de loot, preserva a sessão local e exporta CSV compatível com ao-loot-logger.

O aplicativo também mantém atualização automática, relatórios de falha, bandeja do sistema e início automático. Não executa túnel, otimização de DNS, descoberta de batalhas ou mortes antigas, escaneamento distribuído, captura de mercado nem envio ao AODP.

## Plataformas

### Windows 10/11 (64 bits)

A captura de pacotes requer privilégios de administrador e usa WinDivert, incluído no instalador.

### Linux x86_64

A interface pode ser compilada e usada, mas a captura de pacotes por WinDivert não está disponível.

## Desenvolvimento

Na raiz deste repositório:

```powershell
npm install
npm run tauri dev
```

Para gerar uma build local:

```powershell
npm run build
npm run tauri build
```

## Configuração

A configuração local fica em:

- Windows: `%APPDATA%\ziggs-companion\config.json`
- Linux: `~/.config/ziggs-companion/config.json`
- macOS: `~/Library/Application Support/ziggs-companion/config.json`

As opções controlam o Damage Meter, o Lootlog, o início automático e a minimização para a bandeja. Os controles de captura ficam em **Configurações**; desligar uma captura não apaga os dados já acumulados na sessão.

### Catálogos e renders

O Companion mantém em cache local os nomes de habilidades e itens. Se o backend estiver indisponível ou retornar uma resposta inválida, a captura local continua e o aplicativo mantém o último catálogo válido. Habilidades sem nome usam um identificador visível, e itens desconhecidos usam `IDX_{índice}`. Quando uma arte não está disponível, o nome e um espaço reservado de tamanho fixo continuam visíveis.

### Janela e escala

A janela usa **1024 × 768 pixels lógicos**, sem redimensionamento, maximização ou tela cheia. A escala do sistema operacional pode alterar os pixels físicos, mas não a área lógica do aplicativo.

Use `Ctrl`/`Cmd` + `-` ou `+` — incluindo `=` e as teclas numéricas equivalentes — para ajustar somente a escala da WebView entre 80% e 150%. A preferência é salva localmente e não altera o tamanho da janela.

## Estrutura

```text
├── src/                    Interface React/TypeScript
├── src-tauri/src/
│   ├── lib.rs              Comandos Tauri e ciclo do aplicativo
│   ├── sniffer.rs          Captura WinDivert e processamento Photon
│   ├── photon_parser.rs    Decodificador Photon e acumulador de dano
│   ├── lootlog.rs          Catálogo de itens, sessão e exportação CSV
│   ├── crash_report.rs     Relatórios de falha
│   └── api.rs              Catálogos de habilidades/itens e falhas
└── package.json            Scripts de desenvolvimento e build
```

## Privacidade

Damage Meter e Lootlog são processados localmente. O CSV de loot é salvo localmente. O aplicativo consulta somente os catálogos necessários e pode enviar relatórios de falha pendentes.

## Licença

MIT. Veja [LICENSE](LICENSE).
