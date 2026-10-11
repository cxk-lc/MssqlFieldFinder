@echo off
setlocal enabledelayedexpansion
title 环境诊断 — 库表字段内容检索器

rem ---- 本脚本位于 bat\ 子目录，项目根目录 = 本脚本的上一级 ----
set "BATDIR=%~dp0"
pushd "%BATDIR%.."
set "ROOT=%CD%\"
popd
set "PYDB="

echo ============================================================
echo   环境诊断：逐项探测本机 Python 解释器与运行依赖
echo ============================================================
echo.

echo [1] 候选解释器及其能力
call :probe "%ROOT%runtime\python.exe"
call :probe "%ROOT%python\python.exe"
call :probe "%ROOT%.venv\Scripts\python.exe"
call :probe "%USERPROFILE%\.workbuddy\binaries\python\envs\mssql_scanner\Scripts\python.exe"
for %%V in (313 312 311 310 39) do call :probe "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
for /f "delims=" %%I in ('where python 2^>nul') do call :probe "%%I"
for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do call :probe "%%I"

echo.
echo [2] 本机 SQL Server 相关 ODBC 驱动
if defined PYDB (
    "%PYDB%" -c "import pyodbc;print(chr(10).join('  ' + d for d in pyodbc.drivers()))"
) else (
    echo   未找到已安装 pyodbc 的解释器，改用注册表查询：
    call :list_drivers
)

echo.
echo ------------------------------------------------------------
echo   说明：本工具需要「同时具备 tkinter 与 pyodbc」的解释器。
echo         tkinter 缺失  --^> 重装官方 Python 并勾选 "tcl/tk and IDLE"
echo         pyodbc 缺失   --^> 执行 ^<解释器^> -m pip install pyodbc
echo         ODBC 驱动缺失 --^> 安装 Microsoft ODBC Driver 17 for SQL Server
echo ------------------------------------------------------------
echo.
pause
endlocal
exit /b 0

rem ---- 探测单个解释器 ----
:probe
if "%~1"=="" exit /b
if not exist "%~1" exit /b
set "TK=缺失"
set "OD=缺失"
"%~1" -c "import tkinter" >nul 2>nul
if not errorlevel 1 set "TK=可用"
"%~1" -c "import pyodbc" >nul 2>nul
if not errorlevel 1 (
    set "OD=可用"
    if not defined PYDB set "PYDB=%~1"
)
echo   %~1
echo       tkinter=!TK!   pyodbc=!OD!
exit /b

rem ---- 回退：从注册表读驱动列表 ----
:list_drivers
set "FOUND="
for /f "delims=" %%A in ('reg query "HKLM\SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers" 2^>nul ^| findstr /i "sql server"') do (
    echo   %%A
    set "FOUND=1"
)
if not defined FOUND echo   未读取到注册表项，请手动打开「ODBC 数据源管理器」查看驱动程序页。
exit /b
