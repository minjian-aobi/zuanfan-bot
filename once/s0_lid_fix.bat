@echo off
rem For Modern Standby (S0) laptops: lid close and screen-off are hard
rem triggers into standby that IGNORE power options. This adds the known
rem registry switch that blocks those triggers while plugged in.
rem Run as administrator once, then restart.

net session >nul 2>&1
if errorlevel 1 (
    echo Please run this file as administrator.
    pause
    exit /b 1
)

rem Block modern-standby entry from lid close / display off (AC only)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Power" /v EnforceDisconnectedStandby /t REG_DWORD /d 0 /f

rem Unhide the power options that Modern Standby hides, so the values
rem set by keep_awake.bat (lid action, sleep timeouts) actually apply.
powercfg /attributes SUB_SLEEP STANDBYIDLE -ATTRIB_HIDE
powercfg /attributes SUB_BUTTONS LIDACTION -ATTRIB_HIDE

rem Re-assert: never sleep on AC, lid close does nothing
powercfg /change standby-timeout-ac 0
powercfg -setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg -S SCHEME_CURRENT

echo.
echo Done. RESTART the PC once, then test:
echo   close lid for 5 minutes while the bot runs, open lid,
echo   check logs\run_*.csv for rows written during that window.
pause
