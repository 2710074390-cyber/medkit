; MedKit 安装脚本（Inno Setup 7 / 6 兼容）
; 用法：ISCC.exe medkit.iss  →  dist-installer\MedKit-Setup-0.4.0.exe

#define MyAppName "MedKit"
#define MyAppVersion "0.4.0"
#define MyAppPublisher "MedAgentWork"
#define MyAppExeName "MedKit.exe"
#define MyAppDesc "医学题库工坊 —— 教材 + 教师重点 → 全新题库/押题卷/复习手册（本地生成）"

[Setup]
AppId={{7D3A9C1E-52B4-4F8A-9D1B-0E6C2F4A8B31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments={#MyAppDesc}
DefaultDirName={autopf}\MedKit
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=auto
AllowNoIcons=yes
OutputDir=dist-installer
OutputBaseFilename=MedKit-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
ArchitecturesInstallIn64BitMode=x64compatible
UsedUserAreasWarning=no
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppDesc}
SetupIconFile=medkit.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\MedKit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDesc}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
