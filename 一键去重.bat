@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "PYEXE="
set "ARGS="
if not "%~1"=="" set ARGS=-i "%~1"

if exist "%HERE%python\python.exe" set "PYEXE=%HERE%python\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "C:\Python313\python.exe" set "PYEXE=C:\Python313\python.exe"

if defined PYEXE (
  "%PYEXE%" "%HERE%dedupe.py" --interactive %ARGS%
  goto DONE
)

py -3 -c "print(1)" >nul 2>&1
if not errorlevel 1 (
  py -3 "%HERE%dedupe.py" --interactive %ARGS%
  goto DONE
)

python -c "print(1)" >nul 2>&1
if not errorlevel 1 (
  python "%HERE%dedupe.py" --interactive %ARGS%
  goto DONE
)

echo.
echo [ERROR] Python 3 not found on this computer.
echo         Install Python 3.8+ from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH" during install.
echo.
pause
exit /b 1

:DONE
echo.
pause
