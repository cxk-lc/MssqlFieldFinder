"""连接配置方案的多方案存取（纯逻辑 + JSON 持久化）。

与 ``config.json`` 的分工：

* ``config.json``      —— 主窗口各项设置的「上次使用值」（只写非默认项）；
* ``connections.json`` —— 命名连接方案集合，可保存多套并随时切换。

两者都存放在**项目目录**下，新路径不存在时只读回退到旧的 `%APPDATA%` 路径。
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

#: 归属「连接设置」的字段键。此处显式声明，并由 tests/test_profiles.py 中的
#: 守卫用例校验其与 config.FIELDS 的分组保持一致，避免两处漂移。
CONNECTION_KEYS: tuple[str, ...] = (
    "server",
    "port",
    "database",
    "auth",
    "username",
    "password",
    "driver",
    "encrypt",
    "trust_cert",
    "connect_timeout",
    "query_timeout",
)

SCHEMA_VERSION = 1
DEFAULT_PROFILE_NAME = "默认方案"


@dataclass
class ProfileStore:
    """连接方案集合。

    ``remember`` 记录**每个方案各自**是否保存密码（缺省视为 True）。
    之所以按方案而非全局，是因为不同环境的密码策略往往不同——
    生产库可能不允许落地密码，而测试库无所谓。
    """

    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    active: str = ""
    remember: dict[str, bool] = field(default_factory=dict)

    # ---- 查询 ----
    def names(self) -> list[str]:
        return sorted(self.profiles)

    def has(self, name: str) -> bool:
        return name in self.profiles

    def get(self, name: str) -> dict[str, Any]:
        return dict(self.profiles.get(name, {}))

    def remember_for(self, name: str) -> bool:
        """该方案是否保存密码；未显式设置过视为 True。"""
        return bool(self.remember.get(str(name).strip(), True))

    def pick_active(self) -> str:
        """返回当前应选中的方案名：优先 active，其次首个，都没有则空串。"""
        if self.active and self.active in self.profiles:
            return self.active
        ordered = self.names()
        return ordered[0] if ordered else ""

    # ---- 修改 ----
    def set_remember(self, name: str, flag: bool) -> None:
        key = str(name).strip()
        if key:
            self.remember[key] = bool(flag)

    def upsert(
        self,
        name: str,
        values: Mapping[str, Any],
        remember: bool | None = None,
    ) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("方案名不能为空")
        self.profiles[key] = extract_connection_values(values)
        self.active = key
        if remember is not None:
            self.remember[key] = bool(remember)

    def remove(self, name: str) -> bool:
        existed = self.profiles.pop(name, None) is not None
        self.remember.pop(name, None)
        if existed and self.active == name:
            self.active = ""
        return existed

    def rename(self, old: str, new: str) -> None:
        target = str(new).strip()
        if not target:
            raise ValueError("方案名不能为空")
        if old not in self.profiles or old == target:
            return
        self.profiles[target] = self.profiles.pop(old)
        if old in self.remember:
            self.remember[target] = self.remember.pop(old)
        if self.active == old:
            self.active = target


def extract_connection_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """只挑出连接相关字段，避免把检索条件写进方案。"""
    return {k: values.get(k) for k in CONNECTION_KEYS if k in values}


def blank_passwords(store: ProfileStore) -> None:
    """按各方案自己的「记住密码」开关清空密码（只影响关掉的那个方案）。"""
    for name, payload in store.profiles.items():
        if not store.remember_for(name) and "password" in payload:
            payload["password"] = ""


def store_path() -> Path:
    """方案文件路径：项目目录下（旧 %APPDATA% 路径仅作只读回退）。"""
    return paths.connections_path()


def save(store: ProfileStore, path: Path | None = None) -> Path | None:
    """写入方案文件。

    * 每个方案按其「记住密码」开关决定是否落盘密码；
    * 只写入**有实际内容**的键（默认的「记住密码 = 是」不写），空集合不落盘，
      避免项目目录被空壳文件污染；
    * 以「临时文件 + 原子替换」落盘，写入中断不会损坏既有配置。

    返回写入路径；无可写内容且文件尚不存在时返回 None。
    """
    target = Path(path) if path else store_path()

    snapshot = ProfileStore(
        profiles={name: dict(body) for name, body in store.profiles.items()},
        active=store.active,
        remember=dict(store.remember),
    )
    blank_passwords(snapshot)

    payload: dict[str, Any] = {"version": SCHEMA_VERSION}
    if snapshot.active:
        payload["active"] = snapshot.active
    denied = {
        name: False
        for name in snapshot.profiles
        if not snapshot.remember_for(name)
    }
    if denied:
        payload["remember"] = denied
    if snapshot.profiles:
        payload["profiles"] = snapshot.profiles

    if len(payload) == 1 and not target.exists():
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return target


def load(path: Path | None = None) -> ProfileStore:
    """读取方案文件；缺失或损坏时返回空集合（不抛异常）。

    未显式指定路径时，若项目路径下尚无方案文件而旧 `%APPDATA%` 路径下存在，
    则只读回退到旧文件，避免升级后已保存的方案凭空消失。
    """
    if path is not None:
        source = Path(path)
    else:
        source = paths.resolve_with_legacy(
            store_path(), paths.legacy_connections_path()
        )

    store = ProfileStore()
    if not source.exists():
        return store
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return store
    if not isinstance(raw, dict):
        return store

    profiles_raw = raw.get("profiles")
    if isinstance(profiles_raw, dict):
        for name, body in profiles_raw.items():
            if isinstance(body, dict) and str(name).strip():
                store.profiles[str(name)] = extract_connection_values(body)
    active = raw.get("active")
    store.active = str(active) if isinstance(active, str) else ""

    remember_raw = raw.get("remember")
    if isinstance(remember_raw, dict):
        for name, flag in remember_raw.items():
            if str(name).strip():
                store.remember[str(name)] = bool(flag)

    # 旧版是全局开关（remember_password），迁移为「当时已存在的方案都不记住」
    legacy = raw.get("remember_password")
    if isinstance(legacy, bool) and not legacy:
        for name in store.profiles:
            store.remember.setdefault(name, False)
    return store


def seed_from_legacy_config(
    store: ProfileStore, legacy: Path | None = None
) -> str:
    """升级场景：方案集合为空时，用旧版 `config.json` 里的连接值播种一个方案。

    只读旧文件；仅当旧文件确实带有服务器地址时才生效。
    """
    if store.profiles:
        return ""
    target = Path(legacy) if legacy is not None else paths.legacy_config_path()
    if not target.exists():
        return ""
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(raw, dict):
        return ""
    return ensure_seed(store, raw)


def ensure_seed(store: ProfileStore, values: Mapping[str, Any]) -> str:
    """从历史 config.json 的连接值播种一个默认方案。

    仅当方案集合为空、且历史值里确有服务器地址时执行，保证老用户首次升级
    不会丢失已填写的连接信息。返回新方案名（未播种则为空串）。
    """
    if store.profiles:
        return ""
    if not str(values.get("server") or "").strip():
        return ""
    store.upsert(DEFAULT_PROFILE_NAME, values)
    store.set_remember(DEFAULT_PROFILE_NAME, bool(values.get("remember_password", True)))
    return DEFAULT_PROFILE_NAME
