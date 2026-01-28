@echo off
chcp 65001 >nul
echo Removing ReminderBot from Task Scheduler...
schtasks /delete /tn "ReminderBot" /f
if %errorlevel% == 0 (
    echo Success! ReminderBot has been removed.
) else (
    echo Failed or task not found.
)
pause
