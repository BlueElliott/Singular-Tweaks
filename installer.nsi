; installer.nsi - NSIS installer for Elliott's Singular Controls

!define APP_NAME "ElliottsSingularControls"
!define APP_DISPLAY_NAME "Elliott's Singular Controls"
!define COMPANY_NAME "BlueElliott"
!define APP_EXE "ElliottsSingularControls.exe"

; VERSION passed from GitHub Actions: /DVERSION=v1.0.8
!ifdef VERSION
  !define APP_VERSION "${VERSION}"
!else
  !define APP_VERSION "1.0.14"
!endif

Name "${APP_DISPLAY_NAME} ${APP_VERSION}"
OutFile "dist\${APP_NAME}-Setup-${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\${APP_NAME}"
RequestExecutionLevel user
Unicode true

!include "MUI2.nsh"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"

  ; Save install dir in registry
  WriteRegStr HKCU "Software\${COMPANY_NAME}\${APP_NAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\${COMPANY_NAME}\${APP_NAME}" "Version" "${APP_VERSION}"

  ; Main executable
  File "dist\${APP_EXE}"

  ; Copy static folder (fonts, etc.)
  SetOutPath "$INSTDIR\static"
  File /r "static\*.*"
  SetOutPath "$INSTDIR"

  ; Optional README
  IfFileExists "README.md" 0 +2
    File "README.md"

  ; Start Menu
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"

  ; Desktop shortcut
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"

  ; Register in Add/Remove Programs
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_DISPLAY_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "Publisher" "${COMPANY_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""

  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\Uninstall.exe"

  ; Remove static folder
  RMDir /r "$INSTDIR\static"

  ; Remove shortcuts
  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  ; Clean up registry
  DeleteRegKey HKCU "Software\${COMPANY_NAME}\${APP_NAME}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

  ; Remove install directory (will fail if config files remain, which is fine)
  RMDir "$INSTDIR"
SectionEnd
