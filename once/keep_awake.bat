@echo off
rem Disable sleep and hibernate, lid close does nothing, force-apply now.
rem Run as administrator once, then close the lid and test.

net session >nul 2>&1
if errorlevel 1 (
    echo Please run this file as administrator.
    pause
    exit /b 1
)

powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10

rem Lid close: 0 = do nothing
powercfg -setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0

rem These three often cause sleep even when the above is set:
rem   - Allow wake timers to wake the PC (we want the opposite: never wake, never sleep)
rem   - USB selective suspend can trigger power-state changes
rem   - "Away mode" keeps the PC awake when the lid is closed on some laptops
powercfg -setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 0
powercfg -setacvalueindex SCHEME_CURRENT SUB_USB USBSELECTIVESUSPEND 0
powercfg -setacvalueindex SCHEME_CURRENT SUB_SLEEP AWAYMODE 1

rem Apply the scheme immediately, not just on next boot
powercfg -S SCHEME_CURRENT

rem Also disable hibernate entirely (removes the fallback sleep state)
powercfg /hibernate off

echo.
echo Done. Now: close the lid, wait 5 minutes, open it.
echo If the script is still running in logs, lid close is fixed.
pause
