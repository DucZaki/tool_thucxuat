' Chay Thuc Xuat Tool khong hien cua so terminal.
' Double-click file nay de mo app (thay cho Chay-ThucXuatTool.bat).
Option Explicit

Dim fso, shell, base, exe
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
exe = base & "\ThucXuatTool.exe"

If Not fso.FileExists(exe) Then
  MsgBox "Khong thay ThucXuatTool.exe trong thu muc nay." & vbCrLf & _
         "Hay giai nen lai toan bo file zip, giu nguyen thu muc _internal.", _
         16, "Thuc Xuat"
  WScript.Quit 1
End If

If Not fso.FolderExists(base & "\_internal") Then
  MsgBox "Thieu thu muc _internal canh ThucXuatTool.exe." & vbCrLf & _
         "Chi copy file .exe la KHONG DU — hay giai nen lai file zip.", _
         16, "Thuc Xuat"
  WScript.Quit 1
End If

shell.CurrentDirectory = base
shell.Run """" & exe & """", 1, False
