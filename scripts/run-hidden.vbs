' run-hidden.vbs - launcher khong cua so dung chung cho MOI scheduled task cua GoClaw.
'
' Van de: task chay LogonType=Interactive (S4U can quyen admin, khong dang ky duoc) nen
' pwsh/powershell bung console moi lan chay. Voi GoClaw-OpsWatchdog (15 phut/lan) la 96 lan
' nhay man hinh moi ngay. WScript.Shell.Run voi intWindowStyle=0 KHONG tao console host,
' khac -WindowStyle Hidden (tao roi moi an, van nhay).
'
' Cach dung - arg 0 la interpreter, cac arg con lai truyen thang qua:
'   wscript.exe //nologo run-hidden.vbs pwsh -NoProfile -File D:\path\script.ps1
'   wscript.exe //nologo run-hidden.vbs powershell -NoProfile -File D:\path\x.ps1 -CollectOnly
'
' 'pwsh' resolve qua app-execution alias, KHONG hardcode duong dan WindowsApps co so version
' (path do bien mat moi lan pwsh update -> task fail am tham). 'powershell' = Windows PS 5.1;
' hai cai nay KHONG thay the cho nhau duoc, script 5.1 chay bang pwsh 7 co the vo.
'
' bWaitOnReturn=True de propagate exit code len Task Scheduler. Fire-and-forget se lam task
' luon bao 0 du script crash, va vo hieu hoa MultipleInstances=IgnoreNew.

Option Explicit

Dim sh, fso, exe, args, a, i, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If WScript.Arguments.Count < 1 Then
  WScript.Quit 2   ' thieu interpreter - fail to, dung im lang
End If

exe = WScript.Arguments(0)
Select Case LCase(exe)
  Case "pwsh"
    exe = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe")
    If Not fso.FileExists(exe) Then exe = "pwsh.exe"
  Case "powershell"
    exe = sh.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
End Select

args = ""
For i = 1 To WScript.Arguments.Count - 1
  a = WScript.Arguments(i)
  If InStr(a, " ") > 0 Then a = """" & a & """"   ' Task Scheduler da tach arg, chi boc lai cai co space
  args = args & " " & a
Next

cmd = """" & exe & """" & args

' 0 = khong tao cua so, True = cho xong de tra dung exit code
WScript.Quit sh.Run(cmd, 0, True)
