' Stop the tw-property-sheet using data\psheet.pid (hidden, no console window)
Option Explicit
Dim sh, fso, here, pidFile, pid
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
pidFile = here & "\data\psheet.pid"
If Not fso.FileExists(pidFile) Then
  MsgBox "Not running (data\psheet.pid not found).", 64, "tw-property-sheet"
  WScript.Quit 0
End If
pid = Trim(fso.OpenTextFile(pidFile, 1).ReadAll)
sh.Run "taskkill /PID " & pid & " /T /F", 0, True
fso.DeleteFile pidFile
MsgBox "Stopped.", 64, "tw-property-sheet"
