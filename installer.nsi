; installer.nsi - NSIS installer for SingularTweaks

!define APP_NAME "SingularTweaks"
!define COMPANY_NAME "BlueElliott"
!define APP_EXE "SingularTweaks.exe"

; VERSION will be passed from GitHub Actions as /DVERSION=v1.0.x
!ifdef VERSION
  !define APP_VERSION "${VERSION}"
!else
  !define APP_VERSION "dev"
!endif

; Output installer name (we'll put it in dist\ via OutFile path)
OutFile "dist\${APP_NAME}-Setup-${APP_VERSION}.exe"

; Default installation directory (per-user, no admin needed)
InstallDir "$LOCALAPPDATA\${APP_NAME}"

; Create an uninstaller entry in "Apps & Features"
InstallDirRegKey HKCU "Software\${COMPANY_NAME}\${APP_NAME}" "InstallDir"

RequestExecutionLevel user
Unicode true

;--------------------------------
; Pages

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

;--------------------------------
; Installer Section

Section "Install"
  SetOutPath "$INSTDIR"

  ; Save install dir in registry
  WriteRegStr HKCU "Software\${COMPANY_NAME}\${APP_NAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\${COMPANY_NAME}\${APP_NAME}" "Version" "${APP_VERSION}"

  ; Main files
  File "dist\${APP_EXE}"
  ; Optional docs bundled in your zip step:
  ; We'll include README and version.txt if present
  IfFileExists "release\README.md" 0 +2
    File /oname=README.md "release\README.md"
  IfFileExists "release\version.txt" 0 +2
    File "release\version.txt"

  ; Start Menu folder
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0

  ; Desktop shortcut
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0

  ; Register uninstaller
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "Publisher" "${COMPANY_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

;--------------------------------
; Uninstaller Section

Section "Uninstall"
  ; Remove files
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\version.txt"
  Delete "$INSTDIR\Uninstall.exe"

  ; Remove shortcuts
  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  ; Remove registry keys
  DeleteRegKey HKCU "Software\${COMPANY_NAME}\${APP_NAME}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

  ; Remove install dir
  RMDir "$INSTDIR"
SectionEnd
