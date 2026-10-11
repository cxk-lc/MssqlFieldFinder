@echo off
setlocal enabledelayedexpansion
title 打包 — 库表字段内容检索器

rem ===========================================================================
rem  一键打包脚本：把本项目打成单文件 exe
rem    产物目录 = 程序名（不带版本号与扩展名，取自 src\__init__.py 的 __app_name__）
rem    产物文件 = 程序名 + 版本号 + .exe
rem    即  <程序名>\<程序名>-v<版本>.exe
rem  构建参数写在 MssqlFieldFinder.spec 里，本脚本只负责找环境、读名称与版本、报结果。
rem  用法：bat\build.bat        打包并暂停查看结果
rem        bat\build.bat -y     同上但不暂停（供自动构建调用）
rem  编码约定：GBK + CRLF + 不加 chcp（UTF-8 会让 cmd 出现字节偏移错位）。
rem ===========================================================================

rem ---- 本脚本位于 bat\ 子目录，项目根目录 = 本脚本的上一级 ----
set "BATDIR=%~dp0"
pushd "%BATDIR%.."
set "ROOT=%CD%\"
popd
set "PY="
set "PY_NO_PYI="
set "MFF_NAME="
set "MFF_VERSION="
set "RC=0"
set "NOPAUSE="
if /i "%~1"=="-y" set "NOPAUSE=1"

if not exist "%ROOT%main.py" (
    echo.
    echo [错误] 未在 "%ROOT%" 找到 main.py。
    echo        请保持 bat\ 目录与项目其余文件平级，不要单独拷贝本脚本。
    echo.
    if not defined NOPAUSE pause
    exit /b 1
)

rem ---- 候选解释器：必须同时具备 tkinter + pyodbc + PyInstaller ----
call :try "%ROOT%runtime\python.exe"
call :try "%ROOT%.venv\Scripts\python.exe"
call :try "%USERPROFILE%\.workbuddy\binaries\python\envs\mssql_scanner\Scripts\python.exe"

if not defined PY (
    for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
        if not defined PY call :try "%%I"
    )
)

if not defined PY (
    for %%V in (313 312 311 310 39) do (
        if not defined PY call :try "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    )
)

if not defined PY (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PY call :try "%%I"
    )
)

if defined PY goto :havepy

if defined PY_NO_PYI (
    echo [提示] 找到可用解释器但未装打包工具，正在安装 PyInstaller：
    echo        !PY_NO_PYI!
    echo        仅需一次，之后打包可直接双击本脚本。
    echo.
    "!PY_NO_PYI!" -m pip install --disable-pip-version-check pyinstaller
    if errorlevel 1 (
        echo.
        echo [失败] PyInstaller 安装失败，请检查网络或按「打包说明.md」手工安装。
        if not defined NOPAUSE pause
        exit /b 1
    )
    set "PY=!PY_NO_PYI!"
    goto :havepy
)

echo.
echo [错误] 未找到同时具备 tkinter 与 pyodbc 的 Python 解释器。
echo        打包需要在开发机上完成，请先运行「bat\诊断环境.bat」查看各解释器能力，
echo        或按「打包说明.md」准备环境。
echo.
if not defined NOPAUSE pause
exit /b 1

:havepy
pushd "%ROOT%"

rem ---- 程序名与版本号唯一来源：src\__init__.py ----
rem 注意：for /f 内若写两个带引号的绝对路径，cmd 的引号剥离规则会导致取不到输出，
rem       所以先 pushd 到项目目录，再用相对路径调用 main.py。
for /f "delims=" %%N in ('"%PY%" main.py --app-name 2^>nul') do set "MFF_NAME=%%N"
if not defined MFF_NAME set "MFF_NAME=MssqlFieldFinder"

for /f "delims=" %%V in ('"%PY%" main.py --version 2^>nul') do set "MFF_VERSION=%%V"
if not defined MFF_VERSION set "MFF_VERSION=0.0.0"

echo ============================================================
echo   打包：库表字段内容检索器
echo ------------------------------------------------------------
echo   解释器   : %PY%
echo   程序名   : %MFF_NAME%
echo   版本号   : %MFF_VERSION%
echo   产物目录 : %MFF_NAME%\
echo   产物文件 : %MFF_NAME%\%MFF_NAME%-v%MFF_VERSION%.exe
echo ============================================================
echo.

"%PY%" -m PyInstaller --noconfirm --clean --distpath "%ROOT%%MFF_NAME%" --workpath "%ROOT%build" "%ROOT%MssqlFieldFinder.spec"
set "RC=!ERRORLEVEL!"
popd

if not "!RC!"=="0" (
    echo.
    echo [失败] 打包未成功，退出码 !RC!，请查看上方的 PyInstaller 输出。
    if not defined NOPAUSE pause
    exit /b 1
)

set "OUTDIR=%ROOT%%MFF_NAME%"
set "EXE=%OUTDIR%\%MFF_NAME%-v%MFF_VERSION%.exe"
if not exist "%EXE%" (
    echo.
    echo [失败] 未找到预期产物：%EXE%
    if not defined NOPAUSE pause
    exit /b 1
)

echo.
echo ============================================================
echo   打包完成
echo   产物目录 : %OUTDIR%
for %%A in ("%EXE%") do echo   产物文件 : %%~nxA
for %%A in ("%EXE%") do echo   大小     : %%~zA 字节
"%PY%" -c "import hashlib,sys,pathlib;print('   SHA256   :', hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())" "%EXE%"
echo ------------------------------------------------------------
echo   用法：把该 exe 拷到目标机器双击即可运行（目标机无需安装 Python）。
echo         config.json / connections.json 会生成在 exe 所在目录，即
echo         %MFF_NAME%\ 下，拷贝时连同两个 json 一起带走即可保留方案。
echo         目标机器仍需安装 Microsoft ODBC Driver for SQL Server。
echo   体检：在 cmd 中执行  ^<exe^> --check  可查看环境信息。
echo ============================================================
echo.
if not defined NOPAUSE pause
endlocal
exit /b 0

rem ---- 子过程：探测某解释器是否具备 tkinter + pyodbc（+ PyInstaller）----
:try
if defined PY exit /b
if "%~1"=="" exit /b
if not exist "%~1" exit /b
"%~1" -c "import tkinter, pyodbc" >nul 2>nul
if errorlevel 1 exit /b
"%~1" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    if not defined PY_NO_PYI set "PY_NO_PYI=%~1"
    exit /b
)
set "PY=%~1"
exit /b
