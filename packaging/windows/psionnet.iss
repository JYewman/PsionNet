; PsionNet for Windows: the installer, built with Inno Setup 6 from
; dist\PsionNet, PyInstaller's output:
;
;     iscc /DAppVersion=1.3 packaging\windows\psionnet.iss
;
; Windows has no pppd, so this is PsionNet for devices on the network: the
; netBook Pro on Windows CE or PsionLX. The proxy and its discovery answer
; those devices, so the installer lets PsionNet through Windows Firewall on
; private networks, and takes the rule away again on uninstall.

#ifndef AppVersion
  #define AppVersion "0"
#endif

[Setup]
AppId={{59D61337-7F4A-50E2-BB51-CBD348BA82DE}
AppName=PsionNet
AppVersion={#AppVersion}
AppVerName=PsionNet {#AppVersion}
AppPublisher=Joshua Yewman
AppPublisherURL=https://github.com/JYewman/PsionNet
AppSupportURL=https://github.com/JYewman/PsionNet/issues
DefaultDirName={autopf}\PsionNet
DefaultGroupName=PsionNet
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=PsionNet-{#AppVersion}-Windows-Setup
SetupIconFile=..\..\build_icon\PsionNet.ico
UninstallDisplayIcon={app}\PsionNet.exe
UninstallDisplayName=PsionNet
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: desktopicon; Description: "Put PsionNet on the desktop"; Flags: unchecked

[Files]
Source: "..\..\dist\PsionNet\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\PsionNet"; Filename: "{app}\PsionNet.exe"
Name: "{autodesktop}\PsionNet"; Filename: "{app}\PsionNet.exe"; Tasks: desktopicon

[Run]
; Replace any rule from an earlier install rather than adding a second.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PsionNet"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PsionNet"" dir=in action=allow program=""{app}\PsionNet.exe"" enable=yes profile=private"; Flags: runhidden
Filename: "{app}\PsionNet.exe"; Description: "Start PsionNet"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PsionNet"""; Flags: runhidden; RunOnceId: "PsionNetFirewall"
