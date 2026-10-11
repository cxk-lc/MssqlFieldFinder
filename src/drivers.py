"""ODBC 驱动优选（纯逻辑，无 IO）。

不同机器安装的 SQL Server ODBC 驱动版本差异很大，且连接串里的
``DRIVER={...}`` 必须与已安装驱动的注册名**逐字一致**（含空格），
否则报 "Data source name not found"。因此必须从实际可用列表中挑选。

优先级分三档（同类取版本号更大者）：

1. ``ODBC Driver N for SQL Server`` —— Microsoft ODBC Driver 系列，现行主线；
2. ``SQL Server Native Client N.M`` —— 已停止演进的上一代驱动；
3. ``SQL Server`` —— SQL Server 2000 时代的通用驱动，功能最弱。

档位优先于版本号：即便存在 ``ODBC Driver 11``，也优于 ``Native Client 11.0``，
因为前者是后者的替代品。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_ODBC_VERSION = re.compile(r"odbc driver\s*(\d+)", re.IGNORECASE)
_ODBC_GENERIC = re.compile(r"odbc driver", re.IGNORECASE)
_NATIVE_VERSION = re.compile(r"native client\s*(\d+)", re.IGNORECASE)

_RANK_ODBC = 0
_RANK_NATIVE = 1
_RANK_GENERIC = 2
_RANK_UNKNOWN = 9

#: 官方驱动下载页（界面在缺少驱动时以超链接形式给出）
ODBC_DOWNLOAD_URL = (
    "https://learn.microsoft.com/zh-cn/sql/connect/odbc/"
    "download-odbc-driver-for-sql-server"
)


class DriverNotFound(RuntimeError):
    """本机未安装任何可用的 SQL Server ODBC 驱动。"""


def _rank(name: str) -> tuple[int, int, str]:
    """越小越优。第二项取版本号的相反数，使版本更高者排前。"""
    lower = name.lower()
    m = _ODBC_VERSION.search(lower)
    if m:
        return (_RANK_ODBC, -int(m.group(1)), name)
    if _ODBC_GENERIC.search(lower):
        return (_RANK_ODBC, 0, name)
    m = _NATIVE_VERSION.search(lower)
    if m:
        return (_RANK_NATIVE, -int(m.group(1)), name)
    if "sql server" in lower:
        return (_RANK_GENERIC, 0, name)
    return (_RANK_UNKNOWN, 0, name)


def pick_driver(available: Iterable[str]) -> str:
    """从可用驱动名中挑出最优的一个；无可用项时抛 DriverNotFound。"""
    names = sorted({str(n).strip() for n in available if str(n).strip()})
    if not names:
        raise DriverNotFound("未检测到任何 ODBC 驱动。")

    best = min(names, key=_rank)
    if _rank(best)[0] == _RANK_UNKNOWN:
        raise DriverNotFound(
            "已安装的 ODBC 驱动中找不到 SQL Server 驱动：" + "、".join(names)
        )
    return best


def list_available() -> list[str]:
    """读取本机 ODBC 驱动列表（需要 pyodbc，故在此处惰性导入）。"""
    try:
        import pyodbc  # noqa: PLC0415  —— 惰性导入，保证本模块可无 pyodbc 单测
    except ImportError:  # pragma: no cover - 环境缺失分支
        return []
    return sorted(pyodbc.drivers())


def detect_sql_server_driver(available: Iterable[str]) -> str:
    """返回可用的 SQL Server 驱动名；一个都没有时返回空串（不抛异常）。

    供界面判断是否需要在 ODBC 配置项旁给出「未检测到驱动」的红色提示。
    """
    try:
        return pick_driver(available)
    except DriverNotFound:
        return ""


def has_usable_driver() -> bool:
    """本机是否存在可用的 SQL Server ODBC 驱动。"""
    return bool(detect_sql_server_driver(list_available()))
