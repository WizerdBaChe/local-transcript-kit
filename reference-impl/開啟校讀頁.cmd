@echo off
setlocal enableextensions

rem  Double-click this, or drop a folder of recordings onto it.
rem
rem  ASCII ONLY, deliberately. cmd.exe reads this file's bytes through whatever
rem  code page the console happens to be in, so a Chinese literal stored here is
rem  mangled on any machine whose code page is not the one it was saved under --
rem  silently, and only for the person reading it. Every sentence a human reads
rem  is printed by Python instead, which decides its own encoding. The file NAME
rem  may be Chinese: that is the filesystem's business, not this file's.
rem
rem  CRLF line endings are not cosmetic either: cmd.exe mis-parses multi-line
rem  parenthesised blocks in an LF-only batch file, and the two blocks below are
rem  exactly that shape. `.gitattributes` pins it.

set "LTM_HERE=%~dp0"
set "LTM_PY="

rem  Any Python 3.12 or newer will do. This product imports nothing but the
rem  standard library, so there is nothing to install and nothing to keep in
rem  step -- which is why this launcher can set PYTHONPATH and run from a plain
rem  checkout rather than asking for `pip install` first.
for %%C in ("py -3.12" "py -3" "python") do (
  if not defined LTM_PY (
    %%~C -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
    if not errorlevel 1 set "LTM_PY=%%~C"
  )
)

if not defined LTM_PY (
  echo.
  echo Could not find Python 3.12 or newer on this machine.
  echo.
  echo Install it from python.org and tick "Add python.exe to PATH" during
  echo setup, then double-click this file again. Nothing else is needed:
  echo this tool has no packages to install.
  echo.
  pause
  exit /b 1
)

set "PYTHONPATH=%LTM_HERE%src;%PYTHONPATH%"
%LTM_PY% -m ltm.ui.launch %*
set "LTM_CODE=%ERRORLEVEL%"

rem  A window that vanishes takes the reason with it. Ctrl-C is a clean stop and
rem  returns 0, so this only holds the window open when something actually broke.
if not "%LTM_CODE%"=="0" (
  echo.
  echo It stopped with exit code %LTM_CODE%. The lines above say why.
  pause
)
exit /b %LTM_CODE%
