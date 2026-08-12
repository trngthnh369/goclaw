' goclaw-deadman-hidden.vbs - launcher khong cua so cho goclaw-deadman.ps1
'
' Ly do ton tai: scheduled task chay LogonType=Interactive (S4U can quyen admin,
' khong dang ky duoc) nen pwsh.exe se bung console moi 30 phut. WScript.Shell.Run
' voi intWindowStyle=0 KHONG tao console host -> khong co ca cu nhay man hinh.
'
' bWaitOnReturn=True (khong phai False): wscript CHO ps1 xong roi tra dung exit code.
' Fire-and-forget se lam task luon bao 0 du ps1 crash, va vo hieu hoa MultipleInstances
' =IgnoreNew -> docker exec treo thi cu 30 phut chong them mot pwsh.
'
' Dang ky (khong can admin):
'   schtasks /change /tn "GoClaw-Deadman" /tr "wscript.exe //nologo \"D:\Projects\personal\goclaw\scripts\goclaw-deadman-hidden.vbs\""
'
' Dung app-execution alias cua pwsh (khong hardcode duong dan WindowsApps co version
' - path do bien mat moi lan pwsh update -> task fail am tham).

Option Explicit

Dim sh, fso, pwsh, script, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

pwsh = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe")
If Not fso.FileExists(pwsh) Then pwsh = "pwsh.exe"   ' fallback: giai qua PATH

script = fso.GetParentFolderName(WScript.ScriptFullName) & "\goclaw-deadman.ps1"

cmd = """" & pwsh & """ -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & script & """"

' 0 = cua so an, True = doi ket thuc de propagate exit code len Task Scheduler
WScript.Quit sh.Run(cmd, 0, True)
