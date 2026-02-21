@echo off
chcp 65001 >nul

echo === ReminderBot Task Scheduler Setup ===
echo.

echo [1/2] Registering startup task...
schtasks /create /tn "ReminderBot" /tr "wscript.exe C:\reminder_san\run_hidden.vbs" /sc onstart /delay 0000:30 /rl highest /f
if %errorlevel% == 0 (
    echo   OK: ReminderBot will start on boot.
) else (
    echo   FAILED. Please run as Administrator.
    goto :end
)

echo.
echo [2/2] Registering watchdog task (every 5 min)...
schtasks /create /tn "ReminderBotWatchdog" /tr "wscript.exe C:\reminder_san\watchdog.vbs" /sc minute /mo 5 /rl highest /f
if %errorlevel% == 0 (
    echo   OK: Watchdog will check every 5 minutes.
) else (
    echo   FAILED. Please run as Administrator.
    goto :end
)

echo.
echo Setup complete!
echo   - ReminderBot: starts on boot
echo   - Watchdog: restarts if process dies (5 min check)
echo.
echo To start now: schtasks /run /tn "ReminderBot"

:end
pause
