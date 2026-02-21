' watchdog.ps1 を非表示で実行するラッパー
Set objShell = CreateObject("WScript.Shell")
objShell.Run "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\reminder_san\watchdog.ps1", 0, False
