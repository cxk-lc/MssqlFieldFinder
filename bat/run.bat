@echo off
setlocal enabledelayedexpansion
title 库表字段内容检索器

rem ---- 本脚本位于 bat\ 子目录，项目根目录 = 本脚本的上一级 ----
set "BATDIR=%~dp0"
pushd "%BATDIR%.."
set "ROOT=%CD%\"
popd

if not exist "%ROOT%main.py" (
    echo.
    echo [错误] 未在 "%ROOT%" 找到 main.py。
    echo        请保持 bat\ 目录与项目其余文件平级，不要单独拷贝本脚本。
    echo.
    pause
    exit /b 1
)
set "PY="
set "PY_TK_ONLY="

rem ---- 候选解释器 1：随包分发的运行时 ----
call :try "%ROOT%runtime\python.exe"
call :try "%ROOT%python\python.exe"
call :try "%ROOT%.venv\Scripts\python.exe"

rem ---- 候选解释器 2：本机隔离环境 ----
call :try "%USERPROFILE%\.workbuddy\binaries\python\envs\mssql_scanner\Scripts\python.exe"

rem ---- 候选解释器 3：py 启动器 ----
if not defined PY (
    for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
        if not defined PY call :try "%%I"
    )
)

rem ---- 候选解释器 4：常见用户级安装位置（从新到旧） ----
if not defined PY (
    for %%V in (313 312 311 310 39) do (
        if not defined PY call :try "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    )
)

rem ---- 候选解释器 5：PATH 中的 python ----
if not defined PY (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PY call :try "%%I"
    )
)

if defined PY goto :run

echo.
if defined PY_TK_ONLY (
    echo [缺少依赖] 找到可用的 Python，但未安装 pyodbc：
    echo     !PY_TK_ONLY!
    echo   请执行以下命令安装后重新运行：
    echo     "!PY_TK_ONLY!" -m pip install pyodbc
) else (
    echo [错误] 未找到可用的 Python 解释器。
    echo   请从 python.org 安装官方 Python，安装时务必勾选 "tcl/tk and IDLE"，
    echo   然后执行： python -m pip install pyodbc
    echo   也可运行「bat\诊断环境.bat」查看本机各解释器的能力。
)
echo.
pause
exit /b 1

:run
echo 使用解释器: %PY%
"%PY%" "%ROOT%main.py"
if errorlevel 1 (
    echo.
    echo [错误] 程序异常退出，退出码 %errorlevel%。
    pause
)
endlocal
exit /b 0

rem ---- 子过程：探测某解释器是否同时具备 tkinter 与 pyodbc ----
:try
if defined PY exit /b
if "%~1"=="" exit /b
if not exist "%~1" exit /b
"%~1" -c "import tkinter, pyodbc" >nul 2>nul
if errorlevel 1 (
    if not defined PY_TK_ONLY (
        "%~1" -c "import tkinter" >nul 2>nul
        if not errorlevel 1 set "PY_TK_ONLY=%~1"
    )
    exit /b
)
set "PY=%~1"
exit /b
