; companion/src-tauri/nsis/installer-hooks.nsh
; Hook NSIS do instalador do Ziggs Companion.
; Verifica se o Npcap esta instalado ANTES de copiar os arquivos.
; Se nao estiver, mostra um guia detalhado no idioma do usuario.
;
; Idiomas: PT (2070), EN (1033), ES (1034). Fallback = EN.
; Botoes Sim/Nao/OK/Cancelar sao traduzidos pelo proprio Windows.
;
; Comportamento:
;   Sim      = abre site do Npcap, depois tela de espera
;   Nao      = aborta o instalador (usuario roda de novo depois de instalar Npcap)
;   OK       = re-verifica se Npcap foi instalado
;   Cancelar = aborta o instalador

!macro NSIS_HOOK_PREINSTALL
  npc_check:

  ; Verifica se o Npcap esta instalado (chave de registry)
  ClearErrors
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Npcap" ""
  IfErrors 0 npcap_found
  ReadRegStr $0 HKLM "SOFTWARE\Npcap" ""
  IfErrors 0 npcap_found

  ; Npcap nao encontrado — seleciona mensagem por idioma
  StrCmp $LANGUAGE 2070 npc_pt
  StrCmp $LANGUAGE 1034 npc_es
  Goto npc_en

  npc_pt:
    MessageBox MB_YESNO|MB_ICONEXCLAMATION \
      "O Ziggs Companion precisa do Npcap para funcionar.$\r$\n$\r$\n\
      O Npcap e um driver de captura de rede (gratuito e seguro).$\r$\n\
      Ele nao foi encontrado neste computador.$\r$\n$\r$\n\
      --- COMO INSTALAR O NPCAP ---$\r$\n$\r$\n\
      1. Clique em SIM abaixo para abrir o site do Npcap$\r$\n\
      2. Na pagina, clique no botao 'Download' (verde)$\r$\n\
      3. Baixe o arquivo 'npcap-1.x.x.exe' (o Installer, ~3 MB)$\r$\n\
      4. Execute o arquivo baixado$\r$\n\
      5. Na instalacao, DEIXE as opcoes padrao:$\r$\n\
         - NAO marque 'WinPcap API-compatible Mode'$\r$\n\
         - Deixe 'Support raw 802.11 traffic' marcado$\r$\n\
      6. Clique em Install e aguarde terminar$\r$\n\
      7. Apos terminar, clique em OK na proxima mensagem$\r$\n$\r$\n\
      Clique em SIM para abrir o site e baixar o Npcap.$\r$\n\
      Clique em NAO para cancelar a instalacao." \
      IDYES npc_pt_open
    ; Nao — aborta limpo
    Abort

  npc_en:
    MessageBox MB_YESNO|MB_ICONEXCLAMATION \
      "Ziggs Companion requires Npcap to function.$\r$\n$\r$\n\
      Npcap is a free, safe network capture driver.$\r$\n\
      It was NOT found on this computer.$\r$\n$\r$\n\
      --- HOW TO INSTALL NPCAP ---$\r$\n$\r$\n\
      1. Click YES below to open the Npcap website$\r$\n\
      2. On the page, click the green 'Download' button$\r$\n\
      3. Download the 'npcap-1.x.x.exe' file (the Installer, ~3 MB)$\r$\n\
      4. Run the downloaded file$\r$\n\
      5. During installation, KEEP the default options:$\r$\n\
         - Do NOT check 'WinPcap API-compatible Mode'$\r$\n\
         - Leave 'Support raw 802.11 traffic' checked$\r$\n\
      6. Click Install and wait for it to finish$\r$\n\
      7. After it finishes, click OK on the next message$\r$\n$\r$\n\
      Click YES to open the website and download Npcap.$\r$\n\
      Click NO to cancel the installation." \
      IDYES npc_en_open
    Abort

  npc_es:
    MessageBox MB_YESNO|MB_ICONEXCLAMATION \
      "Ziggs Companion necesita Npcap para funcionar.$\r$\n$\r$\n\
      Npcap es un controlador de captura de red (gratis y seguro).$\r$\n\
      No se encontro en este equipo.$\r$\n$\r$\n\
      --- COMO INSTALAR NPCAP ---$\r$\n$\r$\n\
      1. Haga clic en SI abajo para abrir el sitio de Npcap$\r$\n\
      2. En la pagina, haga clic en el boton 'Download' (verde)$\r$\n\
      3. Descargue el archivo 'npcap-1.x.x.exe' (el Installer, ~3 MB)$\r$\n\
      4. Ejecute el archivo descargado$\r$\n\
      5. Durante la instalacion, MANTENGA las opciones predeterminadas:$\r$\n\
         - NO marque 'WinPcap API-compatible Mode'$\r$\n\
         - Deje 'Support raw 802.11 traffic' marcado$\r$\n\
      6. Haga clic en Install y espere a que termine$\r$\n\
      7. Despues de terminar, haga clic en OK en el siguiente mensaje$\r$\n$\r$\n\
      Haga clic en SI para abrir el sitio y descargar Npcap.$\r$\n\
      Haga clic en NO para cancelar la instalacion." \
      IDYES npc_es_open
    Abort

  ; === TELA DE ESPERA (depois de abrir o site) ===
  ; OK      = instalou o Npcap, re-verifica (volta pro label npc_check)
  ; Cancelar = aborta o instalador

  npc_pt_open:
    ExecShell "open" "https://npcap.com/#download"
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION \
      "O site do Npcap foi aberto no seu navegador.$\r$\n$\r$\n\
      Baixe e instale o Npcap seguindo o tutorial.$\r$\n$\r$\n\
      Clique em OK quando terminar a instalacao$\r$\n\
      para continuar a instalacao do Ziggs Companion.$\r$\n$\r$\n\
      Clique em Cancelar para sair." \
      IDOK npc_check
    Abort

  npc_en_open:
    ExecShell "open" "https://npcap.com/#download"
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION \
      "The Npcap website has been opened in your browser.$\r$\n$\r$\n\
      Download and install Npcap following the tutorial.$\r$\n$\r$\n\
      Click OK when installation is finished$\r$\n\
      to continue the Ziggs Companion installation.$\r$\n$\r$\n\
      Click Cancel to quit." \
      IDOK npc_check
    Abort

  npc_es_open:
    ExecShell "open" "https://npcap.com/#download"
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION \
      "El sitio de Npcap se ha abierto en su navegador.$\r$\n$\r$\n\
      Descargue e instale Npcap siguiendo el tutorial.$\r$\n$\r$\n\
      Haga clic en OK cuando termine la instalacion$\r$\n\
      para continuar la instalacion de Ziggs Companion.$\r$\n$\r$\n\
      Haga clic en Cancelar para salir." \
      IDOK npc_check
    Abort

  npcap_found:
!macroend