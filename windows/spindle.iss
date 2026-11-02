; Inno Setup script for SpindleSetup-x.y.z.exe
;   iscc /DVersion=1.14.0 /DRepo=owner/spindle windows\spindle.iss
; Installs for the current user (no admin prompt) into %LOCALAPPDATA%\Programs\Spindle.
; Settings live in %APPDATA%\Spindle and survive updates and uninstalling.

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef Repo
  #define Repo "voidxzero/spindle"
#endif

[Setup]
AppId={{6F2C9D41-8B3A-4E7F-A1D5-2C8E9B4F7A63}
AppName=Spindle
AppVersion={#Version}
AppVerName=Spindle {#Version}
AppPublisher=Spindle
AppPublisherURL=https://github.com/{#Repo}
AppSupportURL=https://github.com/{#Repo}/issues
AppUpdatesURL=https://github.com/{#Repo}/releases
DefaultDirName={localappdata}\Programs\Spindle
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\dist
OutputBaseFilename=SpindleSetup-{#Version}
SetupIconFile=spindle.ico
UninstallDisplayIcon={app}\Spindle.exe
UninstallDisplayName=Spindle
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; old program files go first, so files dropped from a newer version don't linger
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\bin"

[Files]
Source: "..\dist\Spindle\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Spindle"; Filename: "{app}\Spindle.exe"
Name: "{autodesktop}\Spindle"; Filename: "{app}\Spindle.exe"; Tasks: desktopicon

[Run]
; normal install: offer to start Spindle on the last page
Filename: "{app}\Spindle.exe"; Description: "Start Spindle"; Flags: nowait postinstall skipifsilent
; update from inside Spindle (silent, /RELAUNCH): start the new version by itself
Filename: "{app}\Spindle.exe"; Flags: nowait; Check: Relaunch

[Code]
function Relaunch: Boolean;
begin
  Result := Pos('/RELAUNCH', UpperCase(GetCmdTail)) > 0;
end;
