# ReminderBot Watchdog
# PIDファイルでプロセス生存を判定（WMI CommandLineはpythonw.exeで空になるため不使用）
$pythonw = "C:\Users\asakawagmk\AppData\Local\Programs\Python\Python313\pythonw.exe"
$workDir = "C:\reminder_san"
$pidFile = "C:\reminder_san\bot.pid"
$logFile = "C:\reminder_san\logs\watchdog.log"

$running = $false

if (Test-Path $pidFile) {
    $pidText = (Get-Content $pidFile -Raw).Trim()
    if ($pidText -match '^\d+$') {
        $botPid = [int]$pidText
        try {
            $proc = Get-Process -Id $botPid -ErrorAction Stop
            if ($proc.ProcessName -in @('python', 'pythonw')) {
                $running = $true
            }
        } catch {
            # プロセスが存在しない → 再起動が必要
        }
    }
}

if (-not $running) {
    $p = Start-Process -FilePath $pythonw -ArgumentList "main.py" -WorkingDirectory $workDir -WindowStyle Hidden -PassThru
    $p.Id | Set-Content $pidFile
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "$ts - ReminderBot was not running. Restarted. (PID $($p.Id))"
}
