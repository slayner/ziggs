; companion/src-tauri/nsis/installer-hooks.nsh
; Hook NSIS do instalador do Ziggs Companion.
; Verifica se o Npcap esta instalado ANTES de copiar os arquivos do companion.
; Se nao estiver, mostra um guia detalhado e aborta a instalacao.
;
; O instalador free do Npcap nao suporta /S (silent install e OEM-only),
; e a licenca free proibe redistribuicao embutida. Entao o usuario instala manualmente.

!macro NSIS_HOOK_PREINSTALL
  ; Verifica se o Npcap esta instalado (chave de registry existe?)
  ClearErrors
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Npcap" ""
  IfErrors 0 npcap_found
  ReadRegStr $0 HKLM "SOFTWARE\Npcap" ""
  IfErrors 0 npcap_found

  ; Npcap nao encontrado - mostra guia detalhado e aborta
  MessageBox MB_YESNO|MB_ICONEXCLAMATION \
    "O Ziggs Companion precisa do Npcap para capturar pacotes do Albion.$\r$\n$\r$\n\
    O Npcap NAO foi encontrado neste computador.$\r$\n$\r$\n\
    === Como instalar o Npcap ===$\r$\n$\r$\n\
    Passo 1: Clique em 'Sim' para abrir npcap.com no seu browser$\r$\n\
    Passo 2: Baixe o 'Npcap Installer' (versao gratuita, ~3MB)$\r$\n\
    Passo 3: Execute o instalador do Npcap$\r$\n\
    Passo 4: Na tela de instalacao, mantenha as opcoes padrao:$\r$\n\
             - NAO marque 'WinPcap API-compatible Mode'$\r$\n\
             - MARQUE 'Install Npcap in WinPcap API-compatible Mode' se aparecer$\r$\n\
    Passo 5: Conclua a instalacao do Npcap$\r$\n\
    Passo 6: Volte aqui e execute este instalador novamente$\r$\n$\r$\n\
    Deseja abrir a pagina de download do Npcap agora?" \
    IDYES open_npcap_download
  ; Usuario clicou 'Nao' - aborta a instalacao do companion
  Abort

  open_npcap_download:
    ExecShell "open" "https://npcap.com/#download"
    Abort

  npcap_found:
!macroend