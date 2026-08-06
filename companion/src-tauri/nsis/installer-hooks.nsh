; companion/src-tauri/nsis/installer-hooks.nsh
; Hook NSIS do instalador do Ziggs Companion.
; Verifica se o Npcap esta instalado ANTES de copiar os arquivos do companion.
; Se nao estiver, mostra um guia detalhado e aborta a instalacao.
;
; O instalador free do Npcap nao suporta /S (silent install e OEM-only),
; e a licenca free proibe redistribuicao embutida. Entao o usuario instala manualmente.
;
; Idiomas suportados: pt, en, es (mesmos do site/companion).
; Detecta o idioma do Windows via registry; fallback = ingles.

; --- Strings por idioma ---
; PT
LangString NpcapTitle ${LANG_PORTUGUESE} "Npcap necessario"
LangString NpcapBody ${LANG_PORTUGUESE} "O Ziggs Companion precisa do Npcap para capturar pacotes do Albion.$\r$\n$\r$\n$\r$\nO Npcap NAO foi encontrado neste computador.$\r$\n$\r$\n=== Como instalar o Npcap ===$\r$\n$\r$\nPasso 1: Clique em 'Sim' para abrir npcap.com no seu browser$\r$\nPasso 2: Baixe o 'Npcap Installer' (versao gratuita, ~3MB)$\r$\nPasso 3: Execute o instalador do Npcap$\r$\nPasso 4: Na tela de instalacao, mantenha as opcoes padrao$\r$\nPasso 5: Conclua a instalacao do Npcap$\r$\nPasso 6: Volte aqui e execute este instalador novamente$\r$\n$\r$\nDeseja abrir a pagina de download do Npcap agora?"
LangString NpcapYes ${LANG_PORTUGUESE} "Sim, abrir download"
LangString NpcapNo ${LANG_PORTUGUESE} "Nao, cancelar"

; EN
LangString NpcapTitle ${LANG_ENGLISH} "Npcap required"
LangString NpcapBody ${LANG_ENGLISH} "Ziggs Companion requires Npcap to capture Albion packets.$\r$\n$\r$\n$\r$\nNpcap was NOT found on this computer.$\r$\n$\r$\n=== How to install Npcap ===$\r$\n$\r$\nStep 1: Click 'Yes' to open npcap.com in your browser$\r$\nStep 2: Download the 'Npcap Installer' (free version, ~3MB)$\r$\nStep 3: Run the Npcap installer$\r$\nStep 4: Keep the default options during installation$\r$\nStep 5: Finish the Npcap installation$\r$\nStep 6: Come back here and run this installer again$\r$\n$\r$\nOpen the Npcap download page now?"
LangString NpcapYes ${LANG_ENGLISH} "Yes, open download"
LangString NpcapNo ${LANG_ENGLISH} "No, cancel"

; ES
LangString NpcapTitle ${LANG_SPANISH} "Npcap necesario"
LangString NpcapBody ${LANG_SPANISH} "Ziggs Companion necesita Npcap para capturar paquetes de Albion.$\r$\n$\r$\n$\r$\nNpcap NO se encontro en este equipo.$\r$\n$\r$\n=== Como instalar Npcap ===$\r$\n$\r$\nPaso 1: Haga clic en 'Si' para abrir npcap.com en su navegador$\r$\nPaso 2: Descargue el 'Npcap Installer' (version gratuita, ~3MB)$\r$\nPaso 3: Ejecute el instalador de Npcap$\r$\nPaso 4: Mantenga las opciones predeterminadas durante la instalacion$\r$\nPaso 5: Complete la instalacion de Npcap$\r$\nPaso 6: Vuelva aqui y ejecute este instalador de nuevo$\r$\n$\r$\nDesea abrir la pagina de descarga de Npcap ahora?"
LangString NpcapYes ${LANG_SPANISH} "Si, abrir descarga"
LangString NpcapNo ${LANG_SPANISH} "No, cancelar"


!macro NSIS_HOOK_PREINSTALL
  ; Verifica se o Npcap esta instalado (chave de registry existe?)
  ClearErrors
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Npcap" ""
  IfErrors 0 npcap_found
  ReadRegStr $0 HKLM "SOFTWARE\Npcap" ""
  IfErrors 0 npcap_found

  ; Npcap nao encontrado - mostra mensagem no idioma do usuario
  ; NSIS usa $(LangString) que resolve automaticamente pro idioma ativo
  MessageBox MB_YESNO|MB_ICONEXCLAMATION "$(NpcapBody)" IDYES open_npcap_download
  ; Usuario clicou 'Nao' - aborta a instalacao do companion
  Abort

  open_npcap_download:
    ExecShell "open" "https://npcap.com/#download"
    Abort

  npcap_found:
!macroend