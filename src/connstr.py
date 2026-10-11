"""连接串构造（纯逻辑，无 IO）。"""
from __future__ import annotations

from .models import AuthMode, ConnectionProfile


class ProfileError(ValueError):
    """连接参数不完整或非法。"""


def validate(profile: ConnectionProfile) -> list[str]:
    """返回错误列表，空列表表示参数齐备。"""
    errors: list[str] = []
    if not profile.server.strip():
        errors.append("服务器地址不能为空。")
    if not profile.database.strip():
        errors.append("数据库名不能为空。")
    if not profile.driver.strip():
        errors.append("ODBC 驱动未选择。")
    if profile.auth_mode is AuthMode.SQL:
        if not profile.username.strip():
            errors.append("SQL Server 身份验证需要填写用户名。")
        # 密码为空在少数环境（纯 Windows 凭据映射）下可能合法，
        # 但绝大多数场景属误填，故按警告级错误处理。
        if profile.password == "":
            errors.append("SQL Server 身份验证需要填写密码。")
    if profile.port is not None and not (1 <= int(profile.port) <= 65535):
        errors.append("端口号需在 1 ~ 65535 之间。")
    if profile.connect_timeout < 1:
        errors.append("连接超时需大于 0 秒。")
    if profile.query_timeout < 1:
        errors.append("查询超时需大于 0 秒。")
    return errors


def build(profile: ConnectionProfile) -> str:
    """构造 pyodbc 连接串。

    值中出现的 ``;`` 会被 ODBC 解析为分隔符，故一律用 ``{}`` 包裹。
    """
    if not profile.driver.strip():
        raise ProfileError("ODBC 驱动未指定。")

    parts: list[str] = [f"DRIVER={{{profile.driver.strip()}}}"]

    parts.append(f"SERVER={_brace(profile.server_spec)}")
    parts.append(f"DATABASE={_brace(profile.database)}")

    if profile.auth_mode is AuthMode.WINDOWS:
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={_brace(profile.username)}")
        parts.append(f"PWD={_brace(profile.password)}")

    # 显式声明加密策略：新版驱动默认 Encrypt=yes，而大量内网实例无有效证书，
    # 不显式关闭会导致「连接被强制中断」。故默认关闭加密、并信任服务器证书。
    parts.append(f"Encrypt={'yes' if profile.encrypt else 'no'}")
    if profile.encrypt:
        parts.append(
            f"TrustServerCertificate={'yes' if profile.trust_server_certificate else 'no'}"
        )

    parts.append(f"APP={_brace(profile.app_name)}")
    parts.append(f"Connection Timeout={int(profile.connect_timeout)}")
    return ";".join(parts) + ";"


def _brace(value: object) -> str:
    """用 ``{}`` 包裹取值，内部 ``}`` 需双写以符合 ODBC 转义规则。"""
    text = str(value)
    return "{" + text.replace("}", "}}") + "}" if text else ""
