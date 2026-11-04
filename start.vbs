Option Explicit
Dim fs, shell, root, launcher
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fs.GetParentFolderName(WScript.ScriptFullName)
launcher = fs.BuildPath(root, ".runtime\svd.exe")
If Not fs.FileExists(launcher) Then
    MsgBox "Run install-startup.ps1 first. See README.md.", vbExclamation, "Video Downloader Ultra"
    WScript.Quit 1
End If
shell.Run Chr(34) & launcher & Chr(34), 0, False
