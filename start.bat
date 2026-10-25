@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title aobi zuanfan bot

rem ================= where the script lives =================
set "APPDIR=%~dp0"
if exist "!APPDIR!core\panel.py" goto founddir

for %%D in (
  "D:\aobi"
  "C:\aobi"
  "%USERPROFILE%\aobi"
  "%USERPROFILE%\Desktop\aobi"
  "%USERPROFILE%\Downloads\aobi"
) do (
  if exist "%%~D\core\panel.py" set "APPDIR=%%~D\"
)

if not exist "!APPDIR!core\panel.py" (
  echo [X] core\panel.py not found.
  echo     Keep start.bat inside the aobi folder, or edit APPDIR in this file.
  pause
  exit /b 1
)

:founddir
cd /d "!APPDIR!"
echo [*] folder: %CD%

rem ================= find adb =================
rem priority: adb.exe shipped next to this script > known MuMu paths > system PATH
set "ADBDIR="

if exist "%CD%\adb.exe" set "ADBDIR=%CD%"
if not defined ADBDIR if exist "%CD%\platform-tools\adb.exe" set "ADBDIR=%CD%\platform-tools"

rem adb.exe alone is not enough, it needs its two dlls
if defined ADBDIR if not exist "!ADBDIR!\AdbWinApi.dll" (
  echo.
  echo [X] adb.exe is here but AdbWinApi.dll is missing:
  echo     !ADBDIR!
  echo     Copy these two files from the same MuMu shell folder as adb.exe:
  echo         AdbWinApi.dll
  echo         AdbWinUsbApi.dll
  echo     then run start.bat again.
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
  echo [X] adb not found.
  echo     Fix it in one of these two ways:
  echo.
  echo     A^) Copy adb.exe plus AdbWinApi.dll and AdbWinUsbApi.dll
  echo        from the MuMu install folder ...\MuMuPlayer-12.0\shell\
  echo        into this folder: %CD%
  echo        then run start.bat again. This always works.
  echo.
  echo     B^) Find adb.exe on this PC and paste its folder into the
  echo        list inside start.bat.
  echo        Search command: where /r C:\ adb.exe
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%A in ('where adb') do (
  if not defined ADBSHOWN echo [*] adb: %%A & set "ADBSHOWN=1"
)

rem ================= Python check =================
where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python not found. Run install.bat first.
  pause
  exit /b 1
)

rem ================= deps, install once =================
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

rem ================= launch panel =================
python core\panel.py
pause
