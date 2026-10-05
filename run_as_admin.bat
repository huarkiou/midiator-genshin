@echo off
rem MIDIator launcher for Windows.
rem Run this FROM AN ELEVATED CONTEXT: either "Run as administrator" from the
rem right-click menu, or a shortcut with Advanced -> "Run as administrator" ticked.
rem
rem It does not elevate itself, on purpose. See the message below and the
rem "Running from a double-click" section of README.md.
rem
rem Administrator rights matter because Genshin Impact usually runs elevated, and
rem Windows ignores synthetic input sent from a lower-integrity process.
rem
rem This file is deliberately ASCII-only, and must be saved without a BOM.
rem cmd.exe re-reads a running batch file by byte offset, so non-ASCII bytes or
rem a leading BOM can desync that read and make the script re-execute itself.

title MIDIator
pushd "%~dp0"

net session >nul 2>&1
if not errorlevel 1 goto :elevated

echo.
echo   This script must already be running with administrator rights.
echo.
echo   It deliberately does NOT try to elevate itself. On some systems the
echo   self-elevation call is cancelled by Windows before the consent dialog is
echo   ever drawn, which looks like a hang and then an unexplained failure.
echo.
echo   Start it as administrator in either of these ways:
echo.
echo     1. Right-click this file and choose "Run as administrator".
echo.
echo     2. Recommended: make a shortcut that always elevates.
echo        Right-click this file, then Send to, then Desktop (create shortcut).
echo        Right-click the new shortcut, choose Properties, open the Shortcut
echo        tab, click Advanced, and tick "Run as administrator".
echo        Launching that shortcut elevates before this script starts, so the
echo        prompt is a Windows one and is never skipped.
echo.
echo   See "Running from a double-click" in README.md for more detail.
echo.
if not defined MIDIATOR_NO_PAUSE pause
popd
exit /b 1

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
if not defined MIDIATOR_NO_PAUSE pause
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
    if not defined MIDIATOR_NO_PAUSE pause
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
if not defined MIDIATOR_NO_PAUSE pause
popd
exit /b %MIDIATOR_EXIT%
