"""运行期数据文件的路径解析。

v1.2 起，`config.json` 与 `connections.json` 由 `%APPDATA%\\MssqlFieldFinder`
迁移到**项目目录**下，便于随项目一起备份、审阅，以及在只读用户目录的受限环境中部署。

旧路径仅作**只读回退**：新路径不存在而旧路径存在时读取旧文件，
不删除、不覆盖旧文件，避免升级时静默丢失用户已配置的连接方案。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: 覆盖项目根目录的环境变量
ENV_HOME = "MFF_HOME"

_APP_DIR_NAME = "MssqlFieldFinder"
_CONFIG_NAME = "config.json"
_CONNECTIONS_NAME = "connections.json"


def project_root() -> Path:
    """项目根目录。

    优先级：环境变量 ``MFF_HOME`` > 打包后的可执行文件所在目录 > 源码所在项目根。
    """
    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):  # PyInstaller 等打包场景
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def config_path() -> Path:
    return project_root() / _CONFIG_NAME


def connections_path() -> Path:
    return project_root() / _CONNECTIONS_NAME


def _legacy_dir() -> Path:
    base = (
        os.environ.get("APPDATA")
        or os.environ.get("XDG_CONFIG_HOME")
        or str(Path.home())
    )
    return Path(base) / _APP_DIR_NAME


def legacy_config_path() -> Path:
    return _legacy_dir() / _CONFIG_NAME


def legacy_connections_path() -> Path:
    return _legacy_dir() / _CONNECTIONS_NAME


def resolve_with_legacy(primary: Path, legacy: Path) -> Path:
    """主路径不存在而旧路径存在时回退到旧路径（只读，不动旧文件）。"""
    if not primary.exists() and legacy.exists():
        return legacy
    return primary
