; Inno Setup script for UnifiStockWatcher-Setup.exe (Inno Setup 6.x).
;
;   iscc /DAppVersion=3.0.0 installer\UnifiStockWatcher.iss
;
; scripts/build_windows.py runs this after PyInstaller. It installs for the
; current Windows user only, with no admin prompt, into
; %LOCALAPPDATA%\Programs\UniFi Stock Watcher, with a Start menu shortcut and
; an entry in Settings > Apps. The watch list and settings live in %APPDATA%
; and are never touched by installing, updating or uninstalling.
;
; The in-app updater runs this silently with /relaunch=1: it waits for the
; running copy to exit (the app's single-instance mutex) and then starts the
; new one. The app tells an installed copy from a portable one by the
; uninstaller beside it, unins000.exe.
;
; AppId must never change: it is how Windows (and this installer) recognise an
; existing installation to upgrade in place.

#define AppName "UniFi Stock Watcher"
#define AppExe "UnifiStockWatcher.exe"
#define AppMutex "JamesCupps.UnifiStockWatcher"
#define AppUserModelID "JamesCupps.UnifiStockWatcher"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{5B1E0F7A-3C2D-4E8B-9A61-7D2C4F0B8E35}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=James Cupps
AppPublisherURL=https://jamesccupps.github.io/UnifiStockWatcher/
AppSupportURL=https://github.com/jamesccupps/UnifiStockWatcher/issues
AppUpdatesURL=https://github.com/jamesccupps/UnifiStockWatcher/releases
AppCopyright=Copyright (c) James Cupps. MIT license.
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=UnifiStockWatcher-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; The app's own wait below handles a running copy; the Restart Manager would
; only add a prompt.
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An update replaces the program files wholesale, so nothing from an older
; version lingers in _internal.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\UnifiStockWatcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Registry]
; Start with Windows is the app's own setting; uninstalling takes it away.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#AppName}"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Parameters: "--updated"; Flags: nowait; Check: WantsRelaunch

[Code]
function WantsRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;

{ The in-app updater starts Setup and then quits, so a silent run waits a
  little for it to go. Run by hand while the app is open, Setup asks. }
function AppIsGone: Boolean;
var
  i: Integer;
begin
  i := 0;
  while CheckForMutexes('{#AppMutex}') and (i < 100) do
  begin
    Sleep(150);
    i := i + 1;
  end;
  Result := not CheckForMutexes('{#AppMutex}');
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if WizardSilent then
  begin
    Result := AppIsGone;
    exit;
  end;
  while CheckForMutexes('{#AppMutex}') do
  begin
    if MsgBox('{#AppName} is running. Quit it first: right-click its icon in the ' +
              'notification area and choose Quit. Then click Retry.',
              mbError, MB_RETRYCANCEL) = IDCANCEL then
    begin
      Result := False;
      exit;
    end;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  while CheckForMutexes('{#AppMutex}') do
  begin
    if UninstallSilent then
    begin
      Result := AppIsGone;
      exit;
    end;
    if MsgBox('{#AppName} is running. Quit it first: right-click its icon in the ' +
              'notification area and choose Quit. Then click Retry.',
              mbError, MB_RETRYCANCEL) = IDCANCEL then
    begin
      Result := False;
      exit;
    end;
  end;
end;
