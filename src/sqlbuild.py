"""SQL 片段构造（纯逻辑，无 IO）。

集中承载三类易错逻辑，全部可脱离数据库单测：

1. 标识符转义 —— schema/表/列名无法参数化，必须自行转义，是唯一的注入面；
2. LIKE 模式转义 —— 用户输入里的 ``%`` / ``_`` / ``[`` 必须被视为字面量；
3. 排序规则拼装 —— 使大小写敏感度与服务器默认配置解耦。
"""
from __future__ import annotations

import re

from .models import ColumnRef, MatchMode

#: LIKE 语句使用的转义字符（T-SQL 单反斜杠）
LIKE_ESCAPE_CHAR = "\\"

#: 默认排序规则基础名。选用 Latin1_General 而非 Chinese_PRC：
#: 后者属非 Unicode 排序规则、绑定代码页 936，显式套用到其他代码页的
#: varchar 列上会引入隐式转换与报错风险。
DEFAULT_COLLATION_BASE = "Latin1_General"

#: 全库列元数据查询（当前数据库下所有 schema 的用户表 + 视图）
METADATA_SQL = """
SELECT c.TABLE_SCHEMA, c.TABLE_NAME, t.TABLE_TYPE, c.COLUMN_NAME, c.DATA_TYPE
FROM   INFORMATION_SCHEMA.COLUMNS AS c
JOIN   INFORMATION_SCHEMA.TABLES  AS t
       ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
      AND t.TABLE_NAME   = c.TABLE_NAME
WHERE  t.TABLE_TYPE IN ('BASE TABLE', 'VIEW')
ORDER  BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
""".strip()

_SAFE_COLLATION = re.compile(r"^[A-Za-z0-9_]+$")
_COLLATION_SUFFIX = re.compile(
    r"_(?:(?:CI|CS)_[A-Z0-9_]+|BIN2|BIN)$", re.IGNORECASE
)


# ---------------------------------------------------------------- 标识符


def quote_ident(name: str) -> str:
    """把标识符包成 ``[...]``，并将内部 ``]`` 双写。

    ``a]b`` -> ``[a]]b]``。这是标识符层面唯一的注入防线。
    """
    return "[" + str(name).replace("]", "]]") + "]"


def quote_qualified(schema: str, table: str) -> str:
    """``dbo`` + ``订单]表`` -> ``[dbo].[订单]]表]``。"""
    return f"{quote_ident(schema)}.{quote_ident(table)}"


# ---------------------------------------------------------------- LIKE


def escape_like(value: str, escape_char: str = LIKE_ESCAPE_CHAR) -> str:
    """转义 LIKE 模式串中的元字符，使其按字面量匹配。

    顺序要紧：必须**先**转义转义符自身，否则后续插入的转义符会被再次转义。
    """
    text = str(value)
    text = text.replace(escape_char, escape_char + escape_char)
    text = text.replace("%", escape_char + "%")
    text = text.replace("_", escape_char + "_")
    text = text.replace("[", escape_char + "[")
    return text


def build_like_pattern(value: str, mode: MatchMode) -> str:
    """精准模式返回原值；模糊模式返回 ``%值%``（已转义）。"""
    if mode is MatchMode.EXACT:
        return str(value)
    return f"%{escape_like(value)}%"


# ---------------------------------------------------------------- 排序规则


def normalize_collation_base(base: str) -> str:
    """规整排序规则基础名，剥离结尾的 CI/CS/BIN 等修饰后缀。

    ``Latin1_General_CI_AS`` -> ``Latin1_General``；``Chinese_PRC_CS_AS`` -> ``Chinese_PRC``。
    """
    text = (base or "").strip()
    if not text:
        raise ValueError("排序规则基础名不能为空")
    if not _SAFE_COLLATION.match(text):
        raise ValueError(f"排序规则基础名只允许字母、数字与下划线：{base!r}")
    stripped = _COLLATION_SUFFIX.sub("", text)
    return stripped or text


def collation_for(ignore_case: bool, base: str = DEFAULT_COLLATION_BASE) -> str:
    """按大小写策略生成完整排序规则名。"""
    return f"{normalize_collation_base(base)}_{'CI' if ignore_case else 'CS'}_AS"


# ---------------------------------------------------------------- 谓词与语句


def build_content_predicate(
    column: str,
    value: str,
    mode: MatchMode,
    ignore_case: bool = True,
    collation_base: str = DEFAULT_COLLATION_BASE,
) -> tuple[str, tuple]:
    """构造 (SQL 谓词片段, 参数元组)。

    精准 -> ``[col] COLLATE X = ?``
    模糊 -> ``[col] COLLATE X LIKE ? ESCAPE '\\'``
    """
    collation = collation_for(ignore_case, collation_base)
    ident = quote_ident(column)
    if mode is MatchMode.EXACT:
        return f"{ident} COLLATE {collation} = ?", (str(value),)
    pattern = build_like_pattern(value, mode)
    return f"{ident} COLLATE {collation} LIKE ? ESCAPE '{LIKE_ESCAPE_CHAR}'", (pattern,)


def build_object_target(ref: ColumnRef, with_nolock: bool = True) -> str:
    """``[schema].[table]``（可选附加 ``WITH (NOLOCK)``）。"""
    target = quote_qualified(ref.schema, ref.table)
    return f"{target} WITH (NOLOCK)" if with_nolock else target


def build_probe_sql(
    ref: ColumnRef,
    value: str,
    mode: MatchMode,
    ignore_case: bool = True,
    *,
    collation_base: str = DEFAULT_COLLATION_BASE,
    with_nolock: bool = True,
    count_rows: bool = False,
    sample_size: int = 3,
) -> tuple[str, tuple]:
    """构造单列探测语句。

    - ``count_rows=False``（默认）：``SELECT TOP (n) [col] ...`` —— 命中有值时提前返回，
      代价极低，同时可回传样例值以人工核验；
    - ``count_rows=True``：``SELECT COUNT_BIG(*) ...`` —— 给出精确命中行数，代价更高。
    """
    predicate, params = build_content_predicate(
        ref.column, value, mode, ignore_case, collation_base
    )
    target = build_object_target(ref, with_nolock)
    if count_rows:
        return f"SELECT COUNT_BIG(*) FROM {target} WHERE {predicate}", params
    top = max(1, min(int(sample_size), 50))
    return (
        f"SELECT TOP ({top}) {quote_ident(ref.column)} FROM {target} WHERE {predicate}",
        params,
    )


#: 明细窗口单次最多取回的行数（防止误点大表把内存打满）
DETAIL_ROW_LIMIT = 200
DETAIL_ROW_LIMIT_MAX = 5000


def build_detail_sql(
    ref: ColumnRef,
    value: str,
    mode: MatchMode,
    ignore_case: bool = True,
    *,
    collation_base: str = DEFAULT_COLLATION_BASE,
    with_nolock: bool = True,
    limit: int = DETAIL_ROW_LIMIT,
) -> tuple[str, tuple]:
    """构造「查看命中明细」语句：返回该表中满足条件的前 N 行全部列。

    与探测语句共用同一套谓词与排序规则，因此明细窗口看到的行与结果表里
    「命中」的判定完全一致，不会出现「列表说命中、明细却查不到」的矛盾。
    """
    predicate, params = build_content_predicate(
        ref.column, value, mode, ignore_case, collation_base
    )
    target = build_object_target(ref, with_nolock)
    top = max(1, min(int(limit), DETAIL_ROW_LIMIT_MAX))
    return f"SELECT TOP ({top}) * FROM {target} WHERE {predicate}", params
