' Start tw-property-sheet in background with pythonw (no console window). Log: data\psheet.log
Option Explicit
Dim sh, fso, here, exe
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
On Error Resume Next
Dim rc
For Each exe In Array("pythonw", "pyw")
  Err.Clear
  ' 1) check settings first: LAN mode (PSHEET_HOST not 127.0.0.1) without PSHEET_PASSWORD is refused
  rc = sh.Run(exe & " -X utf8 ""main.py"" --check", 0, True)
  If Err.Number = 0 Then
    If rc <> 0 Then
      MsgBox "Refused to start: PSHEET_HOST opens the tool to your network, but PSHEET_PASSWORD (8+ characters) is not set in .env." & vbCrLf & "Set PSHEET_PASSWORD, or change PSHEET_HOST back to 127.0.0.1. Details: data\psheet.log", 48, "tw-property-sheet"
      WScript.Quit 2
    End If
    ' 2) start the server in background
    sh.Run exe & " -X utf8 ""main.py""", 0, False
    WScript.Quit 0
  End If
Next
MsgBox "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (check Add python.exe to PATH).", 48, "tw-property-sheet"
