"""库表字段内容检索器 —— 启动入口。

用法：
    python main.py              启动图形界面
    python main.py --version    打印版本号后退出（供打包脚本读取）
    python main.py --app-name   打印程序名后退出（供打包脚本生成产物名/产物目录）
    python main.py --check      自检：打印版本、配置目录、驱动情况后退出

打包为窗口程序（无控制台）后，``--version`` / ``--app-name`` / ``--check`` 会尝试
附着到调用方的控制台，这样在 cmd 里执行仍能看到输出；附着失败则把结果写到
exe 同级的 ``selfcheck.log``。
"""
from __future__ import annotations

import os
import sys

# 允许以脚本方式直接运行（打包成 exe 后同样成立）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def attach_console() -> bool:
    """窗口程序附着到父进程控制台，让 print 能被看到。

    仅在「打包运行且当前无标准输出」时生效；源码运行不受影响。
    """
    if not getattr(sys, "frozen", False) or sys.stdout is not None:
        return False
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):
            return False
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        return True
    except Exception:  # noqa: BLE001 - 无控制台时静默降级
        return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if "--version" in args:
        attach_console()
        from src import __version__

        print(__version__)
        return 0

    if "--app-name" in args:
        attach_console()
        from src import __app_name__

        print(__app_name__)
        return 0

    if "--check" in args:
        attach_console()
        return _self_check()

    from src.app import main as run_app

    return run_app()


def _self_check() -> int:
    """部署后的快速体检：把关键信息打到标准输出，并落一份日志。

    打包成窗口程序后未必有控制台，因此始终写一份 ``selfcheck.log`` 到
    可执行文件同级目录，便于拷到客户机上排查。

    体检本身要「打不还手」——环境异常时把原因打出来即可，不能反过来崩溃，
    否则用户拿到的最有用的信息就丢了。
    """
    from src import __app_name__, __version__, config, drivers, paths, profiles

    lines = [
        f"程序名          : {__app_name__}",
        f"版本            : {__version__}",
        f"是否打包运行    : {bool(getattr(sys, 'frozen', False))}",
        f"可执行文件      : {sys.executable}",
    ]

    root = None
    try:
        root = paths.project_root()
        available = drivers.list_available()
        lines += [
            f"配置目录        : {root}",
            f"设置文件        : {paths.config_path()}（存在：{paths.config_path().exists()}）",
            f"方案文件        : {paths.connections_path()}（存在：{paths.connections_path().exists()}）",
            f"并发默认(按核数): {config.default_workers()}（本机 {os.cpu_count()} 核）",
            f"ODBC 驱动       : {'、'.join(available) or '未检测到'}",
            f"可用 SQL 驱动   : {drivers.detect_sql_server_driver(available) or '无'}",
            f"已存连接方案    : {'、'.join(profiles.load().names()) or '无'}",
        ]
    except OSError as exc:
        lines.append(f"环境探测失败    : {exc}")

    text = "\n".join(lines)
    print(text)
    if root is not None:
        try:
            (root / "selfcheck.log").write_text(text + "\n", encoding="utf-8")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
