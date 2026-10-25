@echo off
rem Roll back everything s0_lid_fix.bat changed. Run as administrator, then restart.

net session >nul 2>&1
if errorlevel 1 (
    echo Please run this file as administrator.
    pause
    exit /b 1
)

rem Remove the registry switch
reg delete "HKLM\SYSTEM\CurrentControlSet\Control\Power" /v EnforceDisconnectedStandby /f

rem Lid close back to default (1 = sleep)
powercfg -setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1

rem Re-hide the power options we unhid
powercfg /attributes SUB_SLEEP STANDBYIDLE +ATTRIB_HIDE
powercfg /attributes SUB_BUTTONS LIDACTION +ATTRIB_HIDE

powercfg -S SCHEME_CURRENT

echo.
echo Rolled back. RESTART the PC now.
pause
