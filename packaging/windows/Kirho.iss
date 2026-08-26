#define AppName "Kirho"
#define AppPublisher "Pablo Alfaro"
#define AppURL "https://github.com/pabdan2003/Kirho"
#define AppExeName "Kirho.exe"

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{D8EE967C-FAFE-47AF-8F75-CDD9ACBECC1F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={localappdata}\Programs\Kirho
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Kirho-{#AppVersion}-Windows-x64-Setup
SetupIconFile=..\..\assets\kirho.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardImageFile=..\..\assets\windows-installer-sidebar.bmp
WizardSmallImageFile=..\..\assets\windows-installer-small.bmp
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\Kirho\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\Kirho"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Kirho"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.csin"; ValueType: string; ValueName: ""; ValueData: "Kirho.Circuit"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\Kirho.Circuit"; ValueType: string; ValueName: ""; ValueData: "Kirho Circuit"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Kirho.Circuit\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"
Root: HKCU; Subkey: "Software\Classes\Kirho.Circuit\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch Kirho"; Flags: nowait postinstall skipifsilent
