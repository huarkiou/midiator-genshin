@echo off
rem MIDIator launcher for Windows.
rem Double-click to run. Requests administrator rights first, because Genshin
rem Impact usually runs elevated and Windows ignores synthetic input sent from a
rem lower-integrity process.
rem
rem This file is deliberately ASCII-only, and must be saved without a BOM.
rem cmd.exe re-reads a running batch file by byte offset, so non-ASCII bytes or
rem a leading BOM can desync that read and make the script re-execute itself.

title MIDIator
pushd "%~dp0"

net session >nul 2>&1
if not errorlevel 1 goto :elevated

echo.
echo   Administrator rights are needed: Genshin Impact usually runs elevated,
echo   and Windows ignores input sent from a lower-integrity process.
echo.
echo   Approve the prompt to continue, or choose No to cancel.
echo.

rem Relaunch this exact file elevated, preserving any arguments.
set "MIDIATOR_SELF=%~f0"
set "MIDIATOR_SELF_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath $env:MIDIATOR_SELF -ArgumentList $env:MIDIATOR_SELF_ARGS -Verb RunAs -ErrorAction Stop } catch { exit 1 }"
if errorlevel 1 (
    echo   Cancelled, or elevation was refused. Nothing was started.
    echo.
    pause
    popd
    exit /b 1
)

popd
exit /b 0

:elevated

echo.
echo   MIDIator
echo   --------
echo   Folder: %CD%
echo.

where uv >nul 2>&1
if not errorlevel 1 goto :have_uv

rem uv normally installs to %USERPROFILE%\.local\bin, but it can be relocated,
rem in which case the installer records the directory here.
if defined UV_INSTALL_DIR if exist "%UV_INSTALL_DIR%\uv.exe" (
    set "PATH=%UV_INSTALL_DIR%;%PATH%"
    goto :have_uv
)
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    goto :have_uv
)

echo   uv was not found, so the Python environment cannot be managed.
echo.
echo   Install it first, then run this script again:
echo.
echo       powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
echo.
pause
popd
exit /b 1

:have_uv
echo   uv is available.

echo   Checking the environment...
uv sync --quiet
if errorlevel 1 (
    echo.
    echo   "uv sync" failed. Scroll up for the reason.
    echo.
    pause
    popd
    exit /b 1
)

echo.
echo   Starting MIDIator. Press Ctrl+C to stop, or close this window.
echo   Switch to Genshin and open the instrument to play.
echo.

uv run midiator %*
set "MIDIATOR_EXIT=%ERRORLEVEL%"

echo.
if "%MIDIATOR_EXIT%"=="0" (
    echo   MIDIator stopped.
) else (
    echo   MIDIator exited with code %MIDIATOR_EXIT%. Scroll up for the reason.
)
echo.
pause
popd
exit /b %MIDIATOR_EXIT%
