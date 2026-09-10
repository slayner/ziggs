; companion/src-tauri/nsis/installer-hooks.nsh
; Hook NSIS do instalador do Ziggs Companion.
; Nao ha dependencia externa de drivers — WinDivert (captura de pacotes)
; O WinDivert é incluído como recurso no executável.

!macro NSIS_HOOK_PREINSTALL
!macroend