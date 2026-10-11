"""界面字段声明与配置持久化。

采用 schema 驱动 UI：所有可配置项在此声明为数据，由 widgets/app 自动生成控件。
新增一个参数只需增加一行声明，无需改动界面代码。

字段按分组划分：

* **配置方案**（主窗口）—— 存储级偏好；
* **连接设置**（仅弹窗）—— 连接参数不再出现在主窗口，只在新增/编辑方案的弹窗中展示；
* **检索条件** / **高级设置**（主窗口）。
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .models import AuthMode, ConnectionProfile, MatchMode

# ---------------------------------------------------------------- 分组

GROUP_SCHEME = "配置方案"
GROUP_CONNECTION = "连接设置"
GROUP_SEARCH = "检索条件"
GROUP_ADVANCED = "高级设置"

#: 主窗口上渲染的分组；连接设置由弹窗承载，故不在其中
GROUPS: tuple[str, ...] = (GROUP_SEARCH, GROUP_ADVANCED)

# ---------------------------------------------------------------- 并发默认值

MIN_WORKERS = 4
MAX_WORKERS_CAP = 12


def default_workers(cpu_count: int | None = None) -> int:
    """按本机 CPU 核数给出「适中」的并发线程数。

    取值理由：检索是网络 I/O 密集型，线程数低于 CPU 核数没有意义，故下限取 4；
    但目标是一台共享的生产库，线程过多会加剧锁竞争与服务器负载，故上限封顶 12。
    """
    cores = cpu_count if cpu_count else (os.cpu_count() or MIN_WORKERS)
    return max(MIN_WORKERS, min(int(cores), MAX_WORKERS_CAP))


_CPU_CORES = os.cpu_count() or MIN_WORKERS

# ---------------------------------------------------------------- 标签映射

AUTH_LABELS: dict[str, AuthMode] = {
    AuthMode.SQL.label: AuthMode.SQL,
    AuthMode.WINDOWS.label: AuthMode.WINDOWS,
}
MODE_LABELS: dict[str, MatchMode] = {
    MatchMode.EXACT.label: MatchMode.EXACT,
    MatchMode.FUZZY.label: MatchMode.FUZZY,
}
DEFAULT_AUTH_LABEL = AuthMode.SQL.label
DEFAULT_MODE_LABEL = MatchMode.FUZZY.label


@dataclass(frozen=True)
class Field:
    """一个可配置项的声明。"""

    key: str
    label: str
    kind: str = "text"  # text | password | int | choice | bool
    group: str = GROUP_CONNECTION
    default: Any = ""
    hint: str = ""
    choices: tuple[str, ...] = ()
    width: int = 18
    #: 是否写入配置文件
    persist: bool = True
    #: 是否在启动时读回。检索关键词「写入但不读回」——这样用户能在设置文件里
    #: 看到自己搜过什么，而每次启动界面仍是空的。
    restore: bool = True


FIELDS: tuple[Field, ...] = (
    # ---------------- 配置方案（弹窗内的存储级偏好） ----------------
    Field("remember_password", "记住密码", "bool", GROUP_SCHEME, True,
          "各方案独立设置"),
    # ---------------- 连接设置（仅弹窗） ----------------
    Field("server", "服务器/实例", "text", GROUP_CONNECTION, "",
          r"如 10.0.0.8 或 主机\实例"),
    Field("port", "端口", "int", GROUP_CONNECTION, "", "留空用默认 1433", width=10),
    Field("database", "数据库", "text", GROUP_CONNECTION, ""),
    Field("auth", "认证方式", "choice", GROUP_CONNECTION, DEFAULT_AUTH_LABEL,
          choices=(AuthMode.SQL.label, AuthMode.WINDOWS.label), width=18),
    Field("username", "用户名", "text", GROUP_CONNECTION, ""),
    Field("password", "密码", "password", GROUP_CONNECTION, ""),
    Field("driver", "ODBC 驱动", "choice", GROUP_CONNECTION, "",
          "自动优选本机驱动", width=26),
    Field("encrypt", "加密连接", "bool", GROUP_CONNECTION, False,
          "自签证书建议关闭"),
    Field("trust_cert", "信任服务器证书", "bool", GROUP_CONNECTION, True,
          "加密连接时有效"),
    Field("connect_timeout", "连接超时(秒)", "int", GROUP_CONNECTION, 10, "", width=10),
    Field("query_timeout", "查询超时(秒)", "int", GROUP_CONNECTION, 30,
          "单列探测上限", width=10),
    # ---------------- 检索条件（主窗口） ----------------
    Field("field_keyword", "字段名关键词", "text", GROUP_SEARCH, "",
          "必填；留空不检索", restore=False),
    Field("field_mode", "字段匹配模式", "choice", GROUP_SEARCH, DEFAULT_MODE_LABEL,
          choices=(MatchMode.FUZZY.label, MatchMode.EXACT.label), width=18),
    Field("content_keyword", "内容关键词", "text", GROUP_SEARCH, "",
          "留空则不查内容", restore=False),
    Field("content_mode", "内容匹配模式", "choice", GROUP_SEARCH, DEFAULT_MODE_LABEL,
          choices=(MatchMode.FUZZY.label, MatchMode.EXACT.label), width=18),
    Field("ignore_case", "忽略大小写", "bool", GROUP_SEARCH, True,
          "对字段名与内容同时生效"),
    Field("run_content", "启用内容检索", "bool", GROUP_SEARCH, True,
          "关闭则只定位字段"),
    # ---------------- 高级设置（主窗口） ----------------
    Field("max_workers", "并发线程数", "int", GROUP_ADVANCED, 8,
          f"本机 {_CPU_CORES} 核 · 建议 {MIN_WORKERS}~{MAX_WORKERS_CAP}", width=10),
    Field("max_targets", "最大检索列数", "int", GROUP_ADVANCED, 5000,
          "超出部分不再检索", width=12),
    Field("sample_size", "每列样例条数", "int", GROUP_ADVANCED, 3, "1 ~ 50", width=10),
    Field("count_rows", "统计命中行数", "bool", GROUP_ADVANCED, False,
          "关闭则只判定存在"),
    Field("with_nolock", "使用 NOLOCK", "bool", GROUP_ADVANCED, True,
          "避免阻塞业务写入"),
    Field("collation_base", "排序规则基础名", "text", GROUP_ADVANCED, "Latin1_General",
          "留默认即可", width=18),
)

FIELDS_BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}

#: 归属「连接设置」的字段键（供弹窗与方案白名单使用）
CONNECTION_FIELDS: tuple[Field, ...] = tuple(
    f for f in FIELDS if f.group == GROUP_CONNECTION
)


def fields_of(group: str) -> list[Field]:
    return [f for f in FIELDS if f.group == group]


def defaults() -> dict[str, Any]:
    """全部字段的默认值。`max_workers` 按本机核数动态给出。"""
    values = {f.key: f.default for f in FIELDS}
    values["max_workers"] = default_workers()
    return values


def effective_default(key: str) -> Any:
    """某字段的「实际默认值」——可能与声明值不同（如按核数推导的线程数）。"""
    if key == "max_workers":
        return default_workers()
    spec = FIELDS_BY_KEY.get(key)
    return spec.default if spec is not None else None


# ---------------------------------------------------------------- 数值校验


def check_integer(spec: Field, raw: Any) -> str:
    """整数项的输入校验；返回错误描述，合法则返回空串。

    「端口」允许留空（表示使用默认端口），其余整数项不允许为空。
    """
    if spec.kind != "int":
        return ""
    if raw is None or str(raw).strip() == "":
        return "" if spec.key == "port" else f"「{spec.label}」不能为空。"
    if isinstance(raw, bool) or not isinstance(raw, int):
        return f"「{spec.label}」必须是整数（当前输入：{raw}）。"
    return ""


def integer_problems(values: Mapping[str, Any]) -> list[str]:
    """只校验传入值中实际存在的整数项，便于主窗口与弹窗各自复用。"""
    problems: list[str] = []
    for key, raw in values.items():
        spec = FIELDS_BY_KEY.get(key)
        if spec is None:
            continue
        message = check_integer(spec, raw)
        if message:
            problems.append(message)
    return problems


def _coerce_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def to_connection_profile(values: Mapping[str, Any]) -> ConnectionProfile:
    """把一轮界面取值转成引擎可用的连接参数（界面标签 -> 枚举）。"""
    port = values.get("port")
    return ConnectionProfile(
        server=str(values.get("server") or ""),
        database=str(values.get("database") or ""),
        auth_mode=AUTH_LABELS.get(
            str(values.get("auth")), AUTH_LABELS[DEFAULT_AUTH_LABEL]
        ),
        port=port if isinstance(port, int) and not isinstance(port, bool) else None,
        username=str(values.get("username") or ""),
        password=str(values.get("password") or ""),
        driver=str(values.get("driver") or ""),
        encrypt=bool(values.get("encrypt")),
        trust_server_certificate=bool(values.get("trust_cert")),
        connect_timeout=_coerce_int(values.get("connect_timeout"), 10),
        query_timeout=_coerce_int(values.get("query_timeout"), 30),
    )


# ---------------------------------------------------------------- 持久化


def config_path() -> Path:
    """配置文件路径：项目目录下（旧 %APPDATA% 路径仅作只读回退）。"""
    return paths.config_path()


def _is_default(value: Any, default: Any) -> bool:
    """判断取值是否等同于字段默认值。"""
    if isinstance(default, bool):
        return bool(value) is default
    if default is None:
        return value is None or str(value).strip() == ""
    return str(value).strip() == str(default).strip()


def build_payload(
    values: Mapping[str, Any], remember_password: bool
) -> dict[str, Any]:
    """把界面取值裁剪为「需要落盘的内容」——只保留与默认值不同的、且允许写入的项。

    这样配置文件里不会塞满默认值，用户一眼就能看出自己改过什么。
    """
    payload: dict[str, Any] = {}
    for key, raw in values.items():
        spec = FIELDS_BY_KEY.get(key)
        if spec is None or not spec.persist:
            continue
        value = "" if key == "password" and not remember_password else raw
        if _is_default(value, effective_default(key)):
            continue
        payload[key] = value
    return payload


def save(
    values: Mapping[str, Any],
    remember_password: bool,
    path: Path | None = None,
) -> Path | None:
    """写入配置文件。

    返回写入的路径；**没有任何非默认值时返回 None 且不创建文件**，
    以保证项目目录不会被一个空壳配置污染。
    """
    target = Path(path) if path else config_path()
    payload = build_payload(values, remember_password)

    if not payload and not target.exists():
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def load(path: Path | None = None) -> dict[str, Any]:
    """读取配置；文件缺失或损坏时回退到默认值（不抛异常）。

    未显式指定路径时，若项目路径下尚无配置文件而旧 `%APPDATA%` 路径下存在，
    则只读回退到旧文件，避免升级后设置凭空消失。
    """
    if path is not None:
        source = Path(path)
    else:
        source = paths.resolve_with_legacy(config_path(), paths.legacy_config_path())

    values = defaults()
    if not source.exists():
        return values
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return values
    if not isinstance(raw, dict):
        return values
    for key, value in raw.items():
        spec = FIELDS_BY_KEY.get(key)
        # 关键词只写入不读回：用户能在文件里看到搜过什么，界面每次启动仍是空的
        if spec is None or not spec.restore:
            continue
        values[key] = value
    return values
