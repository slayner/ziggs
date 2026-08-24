; companion/src-tauri/nsis/installer-hooks.nsh
; Hook NSIS do instalador do Ziggs Companion.
; Nao ha dependencia externa de drivers — WinDivert (captura de pacotes)
; e wintun (tunel WireGuard) sao bundled como resources no .exe.

!macro NSIS_HOOK_PREINSTALL
!macroend