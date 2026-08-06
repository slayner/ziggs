; companion/src-tauri/nsis/installer-hooks.nsh
; Hook NSIS do instalador do Ziggs Companion.
; Verifica se o Npcap esta instalado ANTES de copiar os arquivos.
; Se nao estiver, mostra um guia detalhado no idioma do usuario e aborta.
;
; Idiomas: PT (2070), EN (1033), ES (1034). Fallback = EN.
; Os botoes Sim/Nao do MessageBox sao traduzidos pelo proprio Windows.

!macro NSIS_HOOK_PREINSTALL
  ; Verifica se o Npcap esta instalado (chave de registry)
  ClearErrors
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Npcap" ""
  IfErrors 0 npcap_found
  ReadRegStr $0 HKLM "SOFTWARE\Npcap" ""
  IfErrors 0 npcap_found

  ; Npcap nao encontrado — seleciona mensagem por idioma
  ; $LANGUAGE e setado pelo NSIS pro idioma ativo (detectado do Windows)
  StrCmp $LANGUAGE 2070 msg_pt    ; Portugues
  StrCmp $LANGUAGE 1034 msg_es    ; Espanhol
  Goto msg_en                     ; Default: Ingles

  msg_pt:
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
      7. Volte aqui e rode este instalador novamente$\r$\n$\r$\n\
      Deseja abrir o site do Npcap agora?" \
      IDYES open_npcap
    Abort

  msg_en:
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
      7. Come back here and run this installer again$\r$\n$\r$\n\
      Open the Npcap website now?" \
      IDYES open_npcap
    Abort

  msg_es:
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
      7. Vuelva aqui y ejecute este instalador de nuevo$\r$\n$\r$\n\
      Desea abrir el sitio de Npcap ahora?" \
      IDYES open_npcap
    Abort

  open_npcap:
    ExecShell "open" "https://npcap.com/#download"
    Abort

  npcap_found:
!macroend