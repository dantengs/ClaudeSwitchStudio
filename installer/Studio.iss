; Build through build-installer.ps1 so the payload safety checks run first.
#ifndef AppVersion
  #define AppVersion "0.4.0-preview"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.4.0"
#endif
#ifndef PayloadDir
  #define PayloadDir SourcePath + "..\packaging\payload"
#endif
#ifndef BuildOutputDir
  #define BuildOutputDir SourcePath + "..\dist"
#endif
#ifndef ChineseLanguageFile
  #define ChineseLanguageFile CompilerPath + "Languages\ChineseSimplified.isl"
#endif

[Setup]
AppId={{6115D5A1-F07B-49F4-B41D-FCB71896493A}
AppName=Claude Switch Studio
AppVersion={#AppVersion}
AppVerName=Claude Switch Studio {#AppVersion}
AppPublisher=Claude Switch Studio
AppComments=Local account and conversation manager; an independent, unofficial application.
VersionInfoVersion={#NumericVersion}.0
VersionInfoDescription=Claude Switch Studio Setup
DefaultDirName={code:GetDefaultDirectory}
DefaultGroupName=Claude Switch Studio
DisableProgramGroupPage=yes
DisableDirPage=no
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
AppMutex=ClaudeSwitchStudio.Runtime
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
AllowNoIcons=yes
OutputDir={#BuildOutputDir}
OutputBaseFilename=ClaudeSwitchStudio-{#AppVersion}-Setup
SetupIconFile={#PayloadDir}\assets\app.ico
UninstallDisplayIcon={app}\ClaudeSwitchStudio.exe
UninstallDisplayName=Claude Switch Studio
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=110
InfoBeforeFile=INSTALL-NOTES.txt
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "{#ChineseLanguageFile}"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimplified.DesktopIcon=创建桌面快捷方式
english.DesktopIcon=Create a desktop shortcut
chinesesimplified.LaunchProgram=启动 Claude Switch Studio
english.LaunchProgram=Launch Claude Switch Studio

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The build script rejects runtime/authentication material before invoking ISCC.
; Do not add [UninstallDelete]: user-created data and backups must survive uninstall.
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "data,data\*,private-auth-backups,private-auth-backups\*,.claude-profiles,.claude-profiles\*,__pycache__,__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Same AppUserModelID as Studio.ps1, so the window and a pinned shortcut share one taskbar button.
Name: "{group}\Claude Switch Studio"; Filename: "{app}\ClaudeSwitchStudio.exe"; WorkingDir: "{app}"; IconFilename: "{app}\ClaudeSwitchStudio.exe"; AppUserModelID: "ClaudeSwitchStudio.App"
Name: "{autodesktop}\Claude Switch Studio"; Filename: "{app}\ClaudeSwitchStudio.exe"; WorkingDir: "{app}"; IconFilename: "{app}\ClaudeSwitchStudio.exe"; AppUserModelID: "ClaudeSwitchStudio.App"; Tasks: desktopicon

[Run]
; Only an unchecked opt-in on the interactive Finish page. Never launches in silent mode.
Filename: "{app}\ClaudeSwitchStudio.exe"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
function GetDefaultDirectory(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\programfile\Claude Switch Studio'
  else
    Result := ExpandConstant('{localappdata}\Programs\Claude Switch Studio');
end;

// The in-app "start with Windows" option writes this value; data and backups are still kept.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'ClaudeSwitchStudio');
end;
