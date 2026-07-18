!macro NSIS_HOOK_POSTINSTALL
  ; Verifica se Npcap ja esta instalado
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Npcap" "InstallDir"
  ${If} $0 == ""
    DetailPrint "Npcap nao encontrado. Abrindo instalador..."
    ExecWait '"$INSTDIR\resources\npcap-installer.exe"' $1
    ${If} $1 == 0
      DetailPrint "Npcap instalado com sucesso."
    ${Else}
      DetailPrint "Aviso: instalacao do Npcap falhou ou foi cancelada (codigo $1)."
      DetailPrint "O sniffer de pacotes nao vai funcionar sem Npcap. Instale manualmente de https://npcap.com/"
    ${EndIf}
  ${Else}
    DetailPrint "Npcap ja instalado em $0"
  ${EndIf}
!macroend