; Runs even when an older desktop updater starts this installer.
; The guard uses exact image paths and retained Windows process handles.
; It does not stop Codex/Claude or write studio data.
!define VCS_INSTALL_GUARD_SOURCE "${__FILEDIR__}\binaries\voice-clone-install-guard-x86_64-pc-windows-msvc.exe"
!include "${__FILEDIR__}\binaries\installer-mcp-sha.nsh"

!macro NSIS_HOOK_PREINSTALL
  Push $0
  Push $1
  InitPluginsDir
  File "/oname=$PLUGINSDIR\voice-clone-install-guard.exe" "${VCS_INSTALL_GUARD_SOURCE}"

  vcs_mcp_guard_retry:
    DetailPrint "Preparing the Voice Clone agent connection for update..."
    nsExec::ExecToStack /TIMEOUT=30000 '"$PLUGINSDIR\voice-clone-install-guard.exe" --install-dir "$INSTDIR"'
    Pop $0
    Pop $1
    StrCmp $0 "0" vcs_mcp_guard_ready
    IfSilent vcs_mcp_guard_abort
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "The Voice Clone agent connection is still in use. Disconnect it in Codex or Claude, then click Retry. Your saved voices and history are kept." IDRETRY vcs_mcp_guard_retry

  vcs_mcp_guard_abort:
    SetErrorLevel 2
    Quit

  vcs_mcp_guard_ready:
    Pop $1
    Pop $0
!macroend

; A host may reconnect after preflight. Never report a successful update if
; the required MCP file was skipped in a later extraction error dialog.
!macro NSIS_HOOK_POSTINSTALL
  Push $0
  Push $1
  nsExec::ExecToStack /TIMEOUT=30000 '"$PLUGINSDIR\voice-clone-install-guard.exe" --install-dir "$INSTDIR" --verify-mcp-sha256 "${VCS_MCP_SHA256}"'
  Pop $0
  Pop $1
  StrCmp $0 "0" vcs_mcp_integrity_ready
  IfSilent vcs_mcp_integrity_abort
  MessageBox MB_OK|MB_ICONEXCLAMATION "The update could not replace the Voice Clone agent connection. Disconnect it in Codex or Claude, then run this installer again. Your saved voices and history are kept."
  vcs_mcp_integrity_abort:
    SetErrorLevel 2
    Quit
  vcs_mcp_integrity_ready:
    Pop $1
    Pop $0
!macroend
