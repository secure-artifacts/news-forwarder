#define MyAppName "国际新闻转发器"
#define MyAppVersion "1.4.0"
#define MyAppPublisher "Secure Artifacts"
#define MyAppExeName "NewsForwarder.exe"

[Setup]
AppId={{D93D3A4B-6BF3-4AC0-BC66-32B77A739407}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\InternationalNewsForwarder
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer-dist-1.4.0
OutputBaseFilename=International-News-Forwarder-1.4.0-Setup
Compression=lzma2/ultra64
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=1500000000
SlicesPerDisk=1
WizardStyle=modern dynamic
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
SetupIconFile=assets\app-icon.ico
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=国际新闻与社交平台动态自动收集、中文摘要及转发工具
SetupLogging=yes
CloseApplications=force
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "dist\NewsForwarder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "config.example.yaml,config.yaml,data\*,*.log"
Source: "config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\app-icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "data\models\*"; DestDir: "{app}\data\models"; Flags: ignoreversion recursesubdirs createallsubdirs nocompression

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\server.out.log"
Type: files; Name: "{app}\server.err.log"
Type: files; Name: "{app}\local-translation.log"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
