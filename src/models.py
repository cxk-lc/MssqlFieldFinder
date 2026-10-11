"""库表字段内容检索器 —— 数据载体与枚举。

本模块不含任何 IO，可被单元测试直接导入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------- 枚举


class MatchMode(str, Enum):
    """匹配模式。"""

    EXACT = "exact"   # 精准：等值比较
    FUZZY = "fuzzy"   # 模糊：包含匹配

    @property
    def label(self) -> str:
        return "精准" if self is MatchMode.EXACT else "模糊"


class AuthMode(str, Enum):
    """认证方式。"""

    SQL = "sql"           # SQL Server 身份验证（用户名 + 密码）
    WINDOWS = "windows"   # Windows 集成认证（当前域账号，免密）

    @property
    def label(self) -> str:
        return "SQL Server 身份验证" if self is AuthMode.SQL else "Windows 集成认证"


class HitStatus(str, Enum):
    """单个检索目标的终态。"""

    MATCH = "命中"
    NO_MATCH = "未命中"
    TIMEOUT = "超时"
    DENIED = "无权限"
    ERROR = "错误"
    CANCELLED = "已取消"

    @property
    def is_success(self) -> bool:
        return self is HitStatus.MATCH or self is HitStatus.NO_MATCH


# ---------------------------------------------------------------- 载体


@dataclass(frozen=True)
class ColumnRef:
    """一个「库表列」的唯一定位符（阶段 A 的输出）。"""

    schema: str
    table: str
    column: str
    object_type: str = "BASE TABLE"
    data_type: str = ""

    @property
    def is_view(self) -> bool:
        return self.object_type.strip().upper() == "VIEW"

    @property
    def object_label(self) -> str:
        return "视图" if self.is_view else "表"

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass
class Hit:
    """阶段 B 对单个 ColumnRef 的检索结论。"""

    ref: ColumnRef
    status: HitStatus
    rows: int | None = None       # 计数模式下的真实命中行数；存在性模式下为 None
    sample: str = ""              # 命中样例值（逗号拼接）
    detail: str = ""              # 错误/告警文本


@dataclass
class ScanStats:
    """一次内容检索的汇总统计。"""

    total_targets: int = 0
    finished: int = 0
    matched: int = 0
    failed: int = 0
    elapsed: float = 0.0
    cancelled: bool = False

    @property
    def pending(self) -> int:
        return max(0, self.total_targets - self.finished)

    @property
    def progress_text(self) -> str:
        return (
            f"{self.finished}/{self.total_targets} 已完成 · "
            f"命中 {self.matched} · 异常 {self.failed} · "
            f"耗时 {self.elapsed:.1f}s"
        )


@dataclass
class ConnectionProfile:
    """连接参数集合，供 connstr / engine 消费。"""

    server: str = ""
    database: str = ""
    auth_mode: AuthMode = AuthMode.SQL
    port: int | None = None
    username: str = ""
    password: str = ""
    driver: str = ""
    encrypt: bool = False
    trust_server_certificate: bool = True
    app_name: str = "MssqlFieldFinder"
    connect_timeout: int = 10
    query_timeout: int = 30

    @property
    def server_spec(self) -> str:
        """pyodbc 的 SERVER 取值：主机[,端口]。"""
        if self.port:
            return f"{self.server},{int(self.port)}"
        return self.server


@dataclass
class SearchRequest:
    """一次完整检索请求：阶段 A 条件 + 阶段 B 条件 + 执行参数。"""

    # 阶段 A
    field_keyword: str = ""
    field_mode: MatchMode = MatchMode.FUZZY
    # 阶段 B
    content_keyword: str = ""
    content_mode: MatchMode = MatchMode.FUZZY
    # 公共
    ignore_case: bool = True
    collation_base: str = "Latin1_General"

    # 执行参数
    max_workers: int = 8
    with_nolock: bool = True
    count_rows: bool = False
    sample_size: int = 3
    max_targets: int = 5000

    # 开关
    run_content_stage: bool = True

    def validate(self) -> list[str]:
        """返回错误信息列表，空列表表示校验通过。"""
        errors: list[str] = []
        if not self.field_keyword.strip():
            errors.append("「字段名关键词」不能为空。")
        if self.run_content_stage and not self.content_keyword.strip():
            errors.append("已启用内容检索，但「内容关键词」为空。")
        if self.max_workers < 1 or self.max_workers > 64:
            errors.append("线程数需在 1 ~ 64 之间。")
        if self.sample_size < 1 or self.sample_size > 50:
            errors.append("样例条数需在 1 ~ 50 之间。")
        if self.max_targets < 1:
            errors.append("最大检索列数需大于 0。")
        if not self.collation_base.strip():
            errors.append("排序规则基础名不能为空。")
        return errors
