"""匹配语义（纯逻辑，无 IO）。

阶段 A（字段名）在客户端完成过滤，因此此处使用 Python 的大小写折叠，
语义完全可控且不依赖服务器排序规则。
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator

from .models import ColumnRef, MatchMode


def _fold(text: str, ignore_case: bool) -> str:
    return text.casefold() if ignore_case else text


def match_text(actual: str, keyword: str, mode: MatchMode, ignore_case: bool) -> bool:
    """通用匹配：精准 = 等值；模糊 = 包含。

    空关键词一律视为不匹配 —— 避免模糊模式下「空串命中全库」这一危险行为。
    """
    if keyword == "":
        return False
    left = _fold(actual, ignore_case)
    right = _fold(keyword, ignore_case)
    if mode is MatchMode.EXACT:
        return left == right
    return right in left


def match_column_name(
    column_name: str, keyword: str, mode: MatchMode, ignore_case: bool
) -> bool:
    """阶段 A：字段名匹配。"""
    return match_text(column_name, keyword, mode, ignore_case)


def filter_columns(
    refs: Iterable[ColumnRef],
    keyword: str,
    mode: MatchMode,
    ignore_case: bool,
) -> list[ColumnRef]:
    """阶段 A：从全库列元数据中筛出字段名命中的列。"""
    return [r for r in refs if match_column_name(r.column, keyword, mode, ignore_case)]


def iter_unique_objects(refs: Iterable[ColumnRef]) -> Iterator[tuple[str, str, str]]:
    """按 (schema, table, object_type) 去重，保持首次出现顺序。"""
    seen: set[tuple[str, str, str]] = set()
    for r in refs:
        key = (r.schema, r.table, r.object_type)
        if key not in seen:
            seen.add(key)
            yield key
