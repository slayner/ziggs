; companion/src-tauri/nsis/installer-hooks.nsh
; Ziggs Companion installer hook.
; The installer does not touch capture drivers. WinDivert is included as an application
; resource. Npcap can't be redistributed: the app itself guides the user to install it.

!macro NSIS_HOOK_PREINSTALL
!macroend

; Windows keeps showing the old icon on pinned taskbar and Start shortcuts after an update, because
; it caches icons by path. SHCNE_ASSOCCHANGED tells the shell to reload them.
!macro NSIS_HOOK_POSTINSTALL
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

; The app creates these itself when it starts with Windows (see src/autostart.rs). An update
; keeps them. Deleting the scheduled task takes administrator rights, so Windows asks, but
; only whoever has one: left behind, it would run that path as administrator at every logon.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "ZiggsCompanion"
    nsExec::Exec 'schtasks /Query /TN ZiggsCompanion'
    Pop $0
    ${If} $0 = 0
      ExecShell "runas" "schtasks.exe" "/Delete /TN ZiggsCompanion /F" SW_HIDE
    ${EndIf}
  ${EndIf}
!macroend