@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
title FB Short Drama Dedupe Tool v2.1

echo ================================================================
echo   FB 短剧素材批量去重工具  v2.1
echo ================================================================
echo.

rem ---------- 1. 查找真正能用的 Python（逐个实测，不能只看 where）----------
set "PYEXE="

rem  1a. WorkBuddy 自带环境（本机一定可用，放最前）
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" call :TRYPATH "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if defined PYEXE goto HAVE_PY

rem  1b. Python 启动器 py -3
where py >nul 2>nul
if errorlevel 1 goto T_PY
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"
if defined PYEXE goto HAVE_PY

rem  1c. PATH 里的 python
:T_PY
for /f "delims=" %%i in ('where python 2^>nul') do call :TRYPATH "%%i"
if defined PYEXE goto HAVE_PY

rem  1d. PATH 里的 python3
for /f "delims=" %%i in ('where python3 2^>nul') do call :TRYPATH "%%i"
if defined PYEXE goto HAVE_PY

rem  1e. 官方安装包的默认位置
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :TRYPATH "%%d\python.exe"
if defined PYEXE goto HAVE_PY
for /d %%d in ("C:\Python3*") do call :TRYPATH "%%d\python.exe"
if defined PYEXE goto HAVE_PY

rem  1f. 都不行，让用户手动指定
echo [错误] 没有找到可用的 Python（需要 3.9 以上）。
echo.
echo   已自动尝试：py / python / python3 以及常见安装位置。
echo   注意：微软应用商店的 python.exe 占位程序不算，它实际不能运行。
echo.
echo   解决办法二选一：
echo     1^) 安装 Python 3.9+ ： https://www.python.org/downloads/
echo        安装时务必勾选 "Add Python to PATH"，然后重新双击本文件。
echo     2^) 把你的 python.exe 直接拖进本窗口，回车。
echo.
set "MANUAL="
set /p MANUAL=   python.exe 路径：
if defined MANUAL call :TRYPATH "%MANUAL%"
if defined PYEXE goto HAVE_PY
echo.
pause
exit /b 1

:HAVE_PY
echo 使用 Python： %PYEXE%
%PYEXE% --version
echo.

rem ---------- 2. 检查 ffmpeg ----------
if exist "%~dp0bin\ffmpeg.exe" goto RUN
where ffmpeg >nul 2>nul
if not errorlevel 1 goto RUN
%PYEXE% -c "import imageio_ffmpeg" >nul 2>nul
if not errorlevel 1 goto RUN

echo [提示] 未发现 ffmpeg，尝试自动安装 imageio-ffmpeg ...
%PYEXE% -m pip install imageio-ffmpeg --quiet --disable-pip-version-check >nul 2>nul
%PYEXE% -c "import imageio_ffmpeg" >nul 2>nul
if not errorlevel 1 goto GOTFF

echo [错误] 缺少 ffmpeg，程序无法运行。
echo.
echo   任选一种方式解决：
echo     1^) 在本窗口执行：  pip install imageio-ffmpeg
echo     2^) 下载 ffmpeg.exe ^( https://www.gyan.dev/ffmpeg/builds/ ^)
echo        放到本文件夹下的 bin 目录内
echo     3^) 设置环境变量 FFMPEG_BIN 指向你的 ffmpeg.exe
echo.
pause
exit /b 1

:GOTFF
echo ffmpeg 已就绪。
echo.

rem ---------- 3. 启动主程序（支持把视频/文件夹拖到 bat 图标上）----------
:RUN
if "%~1"=="" goto RUN0
echo 已接收拖入路径： %~1
echo.
%PYEXE% "%~dp0dedupe.py" --interactive -i "%~1"
goto DONE

:RUN0
%PYEXE% "%~dp0dedupe.py" --interactive

:DONE
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo [提示] 程序退出码 %RC%，若上方有报错请往上翻查看。
echo.
echo 按任意键关闭窗口 ...
pause >nul
exit /b %RC%

rem --- sub routine: verify a python candidate really works ---
rem --- note: do NOT use find/findstr here, Git Bash may shadow them ---
:TRYPATH
if defined PYEXE goto :EOF
set "CAND=%~1"
if not exist "%CAND%" goto :EOF
set "CHK=%CAND:WindowsApps=%"
if not "%CHK%"=="%CAND%" goto :EOF
"%CAND%" -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if errorlevel 1 goto :EOF
set "PYEXE=%CAND%"
goto :EOF
