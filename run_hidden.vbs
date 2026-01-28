' 非表示でPythonスクリプトを実行（タスクスケジューラ用）
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = "C:\reminder_san"
objShell.Run """C:\Users\asakawagmk\AppData\Local\Programs\Python\Python313\pythonw.exe"" main.py", 0, False
