; companion/src-tauri/nsis/installer-hooks.nsh
; Ziggs Companion installer hook.
; No external driver dependency — WinDivert provides packet capture.
; WinDivert is included as an application resource.

!macro NSIS_HOOK_PREINSTALL
!macroend