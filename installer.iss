; Inno Setup script - builds ValorInventoryAgent-Setup.exe (per-user install, no admin rights needed)
#define AppName "Valor Inventory Agent"
#define AppVer "1.0.0"

[Setup]
AppId={{B1F2A6E4-5C7D-4E0B-9A31-7D2C8E5F1A90}
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer_out
OutputBaseFilename=ValorInventoryAgent-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\ValorInventoryAgent.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce

[Files]
Source: "dist\ValorInventoryAgent\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\ValorInventoryAgent.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ValorInventoryAgent.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ValorInventoryAgent.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

; Your data lives in %APPDATA%\ValorInventoryAgent and is NOT removed on uninstall.
