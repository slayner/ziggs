; companion/src-tauri/nsis/installer-hooks.nsh
; Hook NSIS do instalador do Ziggs Companion.
; Nao bloqueia a instalacao se o Npcap estiver ausente — o companion abre
; normalmente, mas as abas Damage Meter e Lootlog ficam bloqueadas e mostram
; um tutorial de instalacao do Npcap ao serem clicadas.
;
; A aba Rota/Tunel funciona sem Npcap (nao captura pacotes, so usa ICMP ping
; e WireGuard tunnel).

!macro NSIS_HOOK_PREINSTALL
!macroend