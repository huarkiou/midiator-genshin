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
echo   A User Account Control prompt will appear and ask whether to allow this.
echo   It is shown on the secure desktop, so the screen may dim or flash and the
echo   prompt may sit behind other windows. Choose Yes.
echo.
echo   If nothing seems to happen, look for the prompt on the taskbar, or press
echo   Alt+Tab. You can also cancel here and use the shortcut method in the
echo   README instead.
echo.

rem Relaunch this exact file elevated, preserving any arguments.
rem No -Wait: the launcher's only job is to get an elevated copy running, and the
rem elevated window has its own console, so this one can close straight away.
rem Exit 9 marks "the exception path", i.e. the prompt was declined or blocked.
set "MIDIATOR_SELF=%~f0"
set "MIDIATOR_SELF_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath $env:MIDIATOR_SELF -ArgumentList $env:MIDIATOR_SELF_ARGS -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 9 }"
set "MIDIATOR_ELEV=%ERRORLEVEL%"
if "%MIDIATOR_ELEV%"=="0" goto :elevated_done

echo.
echo   Elevation did not happen, so MIDIator was not started.
echo.
if "%MIDIATOR_ELEV%"=="9" (
    echo   Windows reported that the operation was cancelled, which means the UAC
    echo   prompt was declined, dismissed, or replaced by a "blocked" dialog.
    echo.
    echo   Run this file again and choose Yes on the prompt. If you did not see a
    echo   prompt at all, it may have appeared on the secure desktop behind other
    echo   windows: check the taskbar and press Alt+Tab.
) else (
    echo   The elevation helper returned an unexpected code ^(%MIDIATOR_ELEV%^).
    echo   Try the shortcut method described in README.md.
)
echo.
echo   To avoid the prompt entirely, make a shortcut to this file, open its
echo   Properties, Shortcut tab, Advanced, and tick "Run as administrator".
echo.
pause
popd
exit /b 1

:elevated_done
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
