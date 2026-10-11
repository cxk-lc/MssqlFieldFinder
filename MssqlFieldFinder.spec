# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包描述 —— **本文件是构建参数的唯一来源**。

执行（由 ``bat\build.bat`` 自动完成，也可手工调用）：

    cd /d <项目根>
    set MFF_NAME=MssqlFieldFinder
    set MFF_VERSION=1.6.0
    python -m PyInstaller --noconfirm --clean ^
        --distpath MssqlFieldFinder --workpath build MssqlFieldFinder.spec

产物：``MssqlFieldFinder\\MssqlFieldFinder-v<版本>.exe``
（目录名 = 程序名，不带版本号与扩展名；文件名 = 程序名 + 版本号）。双击即可运行。

设计要点
--------
* **程序名与版本号由外部注入**：``MFF_NAME`` / ``MFF_VERSION`` 由 ``bat\build.bat`` 从
  ``src\\__init__.py`` 的 ``__app_name__`` / ``__version__`` 读出后注入，spec 只留兜底默认值，
  避免「脚本一套、spec 一套」的漂移。
* **单文件（onefile）**：产物是一个 exe，便于拷给同事；代价是每次启动需先解包到
  临时目录（约 1~3 秒）。若更在意启动速度，把下面的 ``EXE(...)`` 换成
  ``COLLECT(...)`` 形式即为「绿色目录版」，见文末说明。
* **无控制台窗口**：GUI 程序不需要黑框；但保留 ``disable_windowed_traceback=False``，
  这样万一启动即崩溃，仍会弹窗显示 traceback，而不是静默退出。
* **配置就近存放**：程序运行时用 ``sys.executable`` 的父目录作为配置目录，
  因此 exe 放在哪里，``config.json`` / ``connections.json`` 就生成在哪里。
* **显式 hiddenimports**：``pyodbc`` 由 ``src/drivers.py`` 惰性导入（为的是让
  纯逻辑测试不必安装 pyodbc），显式声明可避免静态分析漏掉这个 C 扩展。
"""
import os
from pathlib import Path

# SPECPATH 在不同 PyInstaller 版本里可能是「spec 所在目录」或「spec 文件路径」，
# 这里两种都兼容，避免解析出上一层目录导致找不到 main.py。
_SPEC_PATH = Path(SPECPATH).resolve()  # noqa: F821 - 由 PyInstaller 注入
ROOT = _SPEC_PATH.parent if _SPEC_PATH.is_file() else _SPEC_PATH

BASENAME = os.environ.get("MFF_NAME", "").strip() or "MssqlFieldFinder"
VERSION = os.environ.get("MFF_VERSION", "").strip()
EXE_NAME = f"{BASENAME}-v{VERSION}" if VERSION else BASENAME

#: 明确排除的第三方库：本工具一个都不用，排除后产物体积明显下降。
EXCLUDES = [
    "numpy", "pandas", "scipy", "matplotlib", "PIL", "cv2",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "IPython", "jupyter", "notebook", "nbformat",
    "pytest", "nose", "sqlalchemy", "pymysql", "psycopg2", "MySQLdb",
    "test", "tests", "unittest", "pydoc_data",
]

a = Analysis(  # noqa: F821 - Analysis/PYZ/EXE 由 PyInstaller 注入
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=["pyodbc"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------------------------------------------------------------------------
# 如需「绿色目录版」（启动更快，产物是一个文件夹）：
#   1. 把上面的 name=EXE_NAME 改为 name=BASENAME，并去掉 a.binaries / a.datas；
#   2. 取消下面 COLLECT 的注释。
#   EXE(pyz, a.scripts, [], exclude_binaries=True, name=BASENAME, console=False, ...)
#   COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
#           name=f"{BASENAME}-v{VERSION}" if VERSION else BASENAME)
# ---------------------------------------------------------------------------
