@echo off
chcp 65001 >nul 2>nul
set "PYDIR=C:\Users\白俊华\.workbuddy\binaries\python\envs\default"
if not exist "%PYDIR%\Scripts\python.exe" (
    echo [错误] 没找到工作区自带的 Python 环境。
    echo 请先用该工作区跑一次任务，让 WorkBuddy 完成初始化。
    pause
    exit /b 1
)
"%PYDIR%\Scripts\python.exe" "%~dp0dedupe.py" --interactive
pause
