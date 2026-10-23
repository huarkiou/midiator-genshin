@echo off
rem MIDIator launcher for Windows.
rem Double-click to run. It asks Windows for administrator rights, because
rem Genshin Impact runs elevated and Windows ignores synthetic input sent from a
rem lower-integrity process.
rem
rem If the elevation request is refused before any prompt appears, see the
rem fallback instructions it prints, or "Running from a double-click" in
rem README.md.
rem
rem Options are passed through to MIDIator unchanged, so a shortcut or a
rem command prompt can carry them:
rem
rem     run_as_admin.bat --latency -q
rem
rem This file is deliberately ASCII-only, and must be saved without a BOM.
rem cmd.exe re-reads a running batch file by byte offset, so non-ASCII bytes or
rem a leading BOM can desync that read and make the script re-execute itself.

title MIDIator
pushd "%~dp0"

net session >nul 2>&1
if not errorlevel 1 goto :elevated

echo.
echo   MIDIator needs administrator rights: Genshin Impact runs elevated, and
echo   Windows ignores input sent from a lower-integrity process.
echo.
echo   A User Account Control prompt will appear and ask whether to allow this.
echo   Choose Yes.
echo.

rem Ask Windows to elevate a fresh copy of this script.
rem
rem The obvious form, Start-Process -FilePath "<this.bat>" -Verb RunAs, is
rem unreliable: it depends on the .bat file association, and on some systems it
rem is cancelled by Windows before the consent dialog is drawn, which surfaces as
rem ERROR_CANCELLED with no prompt and nothing in the event log. Elevating
rem cmd.exe directly avoids that association and works the same way the
rem right-click "Run as administrator" entry does.
rem
rem Exit 0 means the elevated copy was launched. Exit 9 means the exception path,
rem i.e. the request never reached a consent dialog.
set "MIDIATOR_SELF=%~f0"
set "MIDIATOR_SELF_ARGS=%*"
rem Always elevate cmd.exe rather than this .bat file, so the file association is
rem not involved. PowerShell is invoked with -Command, and the quoting is kept
rem simple by passing the path through the environment.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a = '/c ' + [char]34 + $env:MIDIATOR_SELF + [char]34 + ' ' + $env:MIDIATOR_SELF_ARGS; try { Start-Process -FilePath cmd.exe -ArgumentList $a -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 9 }"
set "MIDIATOR_ELEV=%ERRORLEVEL%"
if "%MIDIATOR_ELEV%"=="0" goto :elevated_done

echo.
echo   Windows refused the elevation request before showing a prompt.
echo.
echo   Start this file with the right-click menu instead, which does not go
echo   through the same code path:
echo.
echo       Right-click run_as_admin.bat, then "Run as administrator".
echo.
echo   Or make that permanent:
echo.
echo       Right-click this file, Send to, Desktop (create shortcut).
echo       Right-click the shortcut, Properties, Shortcut tab, Advanced, tick
echo       "Run as administrator". Launch that from now on.
echo.
if not defined MIDIATOR_NO_PAUSE pause
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
