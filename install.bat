@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title aobi bot - one click install

set "PYVER=3.12.10"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

echo ==========================================
echo   Step 1 / 4   checking Python
echo ==========================================

call :findpy
if defined PY (
  echo [*] using: !PY!
  "!PY!" --version
  if not errorlevel 1 (
    echo [OK] Python already installed, skipping download.
    goto deps
  )
  echo [!] that python does not run properly, will reinstall.
  set "PY="
)

echo [!] Python not found or broken. Installing automatically...
echo.

where winget >nul 2>nul
if not errorlevel 1 (
  echo ==========================================
  echo   Step 2 / 4   installing Python via winget
  echo ==========================================
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
)

call :findpy
if not defined PY (
  echo ==========================================
  echo   Step 2 / 4   downloading python %PYVER%
  echo ==========================================
  echo     from python.org, about 25 MB, please wait...
  curl -L -o "%TEMP%\python-setup.exe" "%PYURL%"
  if errorlevel 1 (
    echo [X] download failed. Check the network, or install manually from python.org
    echo     and remember to CHECK "Add python.exe to PATH".
    pause
    exit /b 1
  )
  echo [*] installing, a progress window may appear, do not close it...
  start /wait "" "%TEMP%\python-setup.exe" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
  del "%TEMP%\python-setup.exe" >nul 2>nul
  call :findpy
)

if not defined PY (
  echo [X] Python still not detected. Close this window, reopen it and run this file again.
  echo     If it keeps failing, install manually from python.org with "Add python.exe to PATH" checked.
  pause
  exit /b 1
)
echo [*] using: !PY!
"!PY!" --version
echo [OK] Python installed.

:deps
echo.
echo ==========================================
echo   Step 3 / 4   installing opencv / numpy / pytesseract
echo ==========================================

set "APPDIR=%~dp0"
if not exist "!APPDIR!requirements.txt" (
  echo [X] requirements.txt not found next to install.bat.
  echo     Unzip the whole folder first, then run install.bat inside it.
  pause
  exit /b 1
)

cd /d "!APPDIR!"
echo [*] folder: %CD%

rem ---- make sure pip exists at all ----
"!PY!" -m pip --version
if errorlevel 1 (
  echo [!] pip missing, bootstrapping with ensurepip...
  "!PY!" -m ensurepip --upgrade
  "!PY!" -m pip --version
  if errorlevel 1 (
    echo [X] pip is unavailable in this Python.
    echo     Easiest fix: uninstall Python from Settings, then run this file again
    echo     so it installs a clean copy from python.org.
    pause
    exit /b 1
  )
)

rem ---- try the default index first ----
echo [*] installing from pypi.org ...
"!PY!" -m pip install -r requirements.txt
if not errorlevel 1 goto depsok

rem ---- retry with a China mirror ----
echo.
echo [!] pypi.org failed, retrying with the Tsinghua mirror...
"!PY!" -m pip install -i %MIRROR% -r requirements.txt
if not errorlevel 1 goto depsok

rem ---- last resort: user install, mirror, no cache ----
echo.
echo [!] retrying once more with --user ...
"!PY!" -m pip install --user --no-cache-dir -i %MIRROR% -r requirements.txt
if not errorlevel 1 goto depsok

echo.
echo [X] pip install failed. Read the red lines above for the real reason.
echo     Common causes: no internet, company proxy, or antivirus blocking pip.
pause
exit /b 1

:depsok
echo [OK] dependencies installed.
"!PY!" -c "import cv2, numpy; print('cv2', cv2.__version__, '/ numpy', numpy.__version__)"

echo.
echo ==========================================
echo   Step 4 / 4   Tesseract OCR (reads the diamond count)
echo ==========================================
call :findtess
if defined TESS (
  echo [OK] Tesseract already installed: !TESS!
  goto done
)
where winget >nul 2>nul
if errorlevel 1 (
  echo [!] winget not available, cannot install Tesseract automatically.
  goto notess
)
echo [*] installing Tesseract via winget, an installer window may appear...
winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
call :findtess
if defined TESS (
  echo [OK] Tesseract installed: !TESS!
  goto done
)

:notess
echo [!] Tesseract was not installed. The bot still works without it:
echo     it will just ask you to type the diamond count when it starts.
echo     To install it by hand, download the installer from
echo     https://github.com/UB-Mannheim/tesseract/wiki
echo     and keep the default install folder.

:done
echo.
echo ==========================================
echo   All done. Now open MuMu, log into the game,
echo   then double click start.bat
echo ==========================================
pause
exit /b 0

rem ================= helpers =================
:findtess
set "TESS="
for /f "delims=" %%T in ('where tesseract 2^>nul') do if not defined TESS set "TESS=%%T"
for %%T in (
  "%ProgramFiles%\Tesseract-OCR\tesseract.exe"
  "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe"
  "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"
  "%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"
) do (
  if not defined TESS if exist "%%~T" set "TESS=%%~T"
)
exit /b 0

:findpy
set "PY="
for /f "delims=" %%P in ('where python 2^>nul') do (
  echo %%P | find /i "\WindowsApps\" >nul
  if errorlevel 1 if not defined PY set "PY=%%P"
)
if not defined PY if exist "%PYEXE%" set "PY=%PYEXE%"
if not defined PY (
  for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
)
exit /b 0
