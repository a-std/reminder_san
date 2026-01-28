@echo off
chcp 65001 >nul
echo Registering ReminderBot to Task Scheduler...
schtasks /create /tn "ReminderBot" /tr "wscript.exe C:\reminder_san\run_hidden.vbs" /sc onstart /delay 0000:30 /rl highest /f
if %errorlevel% == 0 (
    echo Success! ReminderBot will start automatically on boot.
    echo To start now: schtasks /run /tn "ReminderBot"
) else (
    echo Failed. Please run as Administrator.
)
pause
