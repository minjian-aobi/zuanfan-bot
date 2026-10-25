@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title aobi zuanfan bot - full run

rem 手动一键跑全流程：先清没饭的人、跑关注列表，
rem 没吃满就自动开面板转世界频道，吃满 100 晶钻自己收工。
rem 想什么时候跑就双击这个文件，不跑的时候什么都不用管。

rem ================= where the script lives =================
set "APPDIR=%~dp0"
if exist "!APPDIR!core\follow_list.py" goto founddir

for %%D in (
  "D:\aobi"
  "C:\aobi"
  "%USERPROFILE%\aobi"
  "%USERPROFILE%\Desktop\aobi"
  "%USERPROFILE%\Downloads\aobi"
) do (
  if exist "%%~D\core\follow_list.py" set "APPDIR=%%~D\"
)

if not exist "!APPDIR!core\follow_list.py" (
  echo [X] core\follow_list.py not found.
  echo     Keep start_full.bat inside the aobi folder, or edit APPDIR in this file.
  pause
  exit /b 1
)

:founddir
cd /d "!APPDIR!"
echo [*] folder: %CD%

rem ================= find adb =================
set "ADBDIR="

if exist "%CD%\adb.exe" set "ADBDIR=%CD%"
if not defined ADBDIR if exist "%CD%\platform-tools\adb.exe" set "ADBDIR=%CD%\platform-tools"

if defined ADBDIR if not exist "!ADBDIR!\AdbWinApi.dll" (
  echo.
  echo [X] adb.exe is here but AdbWinApi.dll is missing:
  echo     !ADBDIR!
  echo     Copy AdbWinApi.dll and AdbWinUsbApi.dll next to adb.exe, then retry.
  echo.
  pause
  exit /b 1
)

if not defined ADBDIR (
  for %%D in (
    "C:\Program Files\Netease\MuMuPlayer-12.0\shell"
    "C:\Program Files (x86)\Netease\MuMuPlayer-12.0\shell"
    "D:\Program Files\Netease\MuMuPlayer-12.0\shell"
    "D:\Program Files (x86)\Netease\MuMuPlayer-12.0\shell"
    "E:\Program Files\Netease\MuMuPlayer-12.0\shell"
    "D:\Netease\MuMuPlayer-12.0\shell"
    "E:\Netease\MuMuPlayer-12.0\shell"
    "C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell"
    "D:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell"
    "C:\Program Files\MuMuPlayer-12.0\shell"
    "D:\MuMuPlayer-12.0\shell"
  ) do (
    if not defined ADBDIR if exist "%%~D\adb.exe" set "ADBDIR=%%~D"
  )
)

rem not in the usual places: ask MuMu itself where it is installed
rem (running MuMu processes + installed-programs registry)
if not defined ADBDIR (
  for /f "usebackq delims=" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\tools\find_adb.ps1"`) do (
    if not defined ADBDIR set "ADBDIR=%%A"
  )
)

if defined ADBDIR set "PATH=!ADBDIR!;%PATH%"

where adb >nul 2>nul
if errorlevel 1 (
  echo.
  echo [X] adb not found. See start.bat for how to fix this.
  echo.
  pause
  exit /b 1
)

rem ================= Python check =================
where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python not found. Run install.bat first.
  pause
  exit /b 1
)

python -c "import cv2, numpy" >nul 2>nul
if errorlevel 1 (
  echo [*] installing dependencies, first run only...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [X] pip install failed. Run install.bat, it has mirror retries.
    pause
    exit /b 1
  )
)

rem ================= connect emulator =================
adb connect 127.0.0.1:16384 >nul 2>nul
adb devices
echo.
echo [*] Need a line "127.0.0.1:16384   device" above. If missing, open MuMu first.
echo.

rem ================= launch full flow =================
rem follow_list.py: 每日清理 + 关注列表巡回，不够自动转世界频道（开面板）。
rem 中途想停就关面板窗口或在这个窗口按 Ctrl+C。
python core\follow_list.py
pause
