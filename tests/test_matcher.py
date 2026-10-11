"""matcher 单元测试：字段名匹配语义与过滤。"""
from __future__ import annotations

import unittest

from src import matcher
from src.models import ColumnRef, MatchMode

E = MatchMode.EXACT
F = MatchMode.FUZZY


def ref(col: str, table: str = "T1", schema: str = "dbo", kind: str = "BASE TABLE") -> ColumnRef:
    return ColumnRef(schema, table, col, kind, "nvarchar")


class TestMatchColumnName(unittest.TestCase):
    # ---- 精准 ----
    def test_exact_same(self) -> None:
        self.assertTrue(matcher.match_column_name("UserID", "UserID", E, True))

    def test_exact_case_insensitive_hit(self) -> None:
        self.assertTrue(matcher.match_column_name("UserID", "userid", E, True))

    def test_exact_case_sensitive_miss(self) -> None:
        self.assertFalse(matcher.match_column_name("UserID", "userid", E, False))

    def test_exact_not_substring(self) -> None:
        self.assertFalse(matcher.match_column_name("CreateUserID", "UserID", E, True))

    # ---- 模糊 ----
    def test_fuzzy_contains(self) -> None:
        self.assertTrue(matcher.match_column_name("CreateUserID", "userid", F, True))

    def test_fuzzy_case_sensitive_miss(self) -> None:
        self.assertFalse(matcher.match_column_name("CreateUserID", "userid", F, False))

    def test_fuzzy_case_sensitive_hit(self) -> None:
        self.assertTrue(matcher.match_column_name("CreateUserID", "UserID", F, False))

    def test_fuzzy_prefix_and_suffix(self) -> None:
        self.assertTrue(matcher.match_column_name("RemarkText", "Remark", F, True))
        self.assertTrue(matcher.match_column_name("RemarkText", "Text", F, True))

    def test_fuzzy_not_present(self) -> None:
        self.assertFalse(matcher.match_column_name("RemarkText", "Note", F, True))

    def test_chinese_keyword(self) -> None:
        self.assertTrue(matcher.match_column_name("订单编号", "订单", F, True))

    # ---- 边界 ----
    def test_empty_keyword_never_matches(self) -> None:
        self.assertFalse(matcher.match_column_name("Anything", "", E, True))
        self.assertFalse(matcher.match_column_name("Anything", "", F, True))

    def test_empty_actual_with_empty_keyword(self) -> None:
        self.assertFalse(matcher.match_column_name("", "", E, True))

    def test_fullwidth_and_halfwidth_are_distinct(self) -> None:
        self.assertFalse(matcher.match_column_name("ABC", "ＡＢＣ", E, True))


class TestFilterColumns(unittest.TestCase):
    def setUp(self) -> None:
        self.refs = [
            ref("UserID", "Users"),
            ref("CreateUserID", "Users"),
            ref("OrderNo", "Orders"),
            ref("订单编号", "Orders", "dbo", "VIEW"),
        ]

    def test_exact_filter(self) -> None:
        got = matcher.filter_columns(self.refs, "UserID", E, True)
        self.assertEqual([r.column for r in got], ["UserID"])

    def test_fuzzy_filter(self) -> None:
        got = matcher.filter_columns(self.refs, "userid", F, True)
        self.assertEqual([r.column for r in got], ["UserID", "CreateUserID"])

    def test_filter_preserves_order(self) -> None:
        got = matcher.filter_columns(self.refs, "o", F, True)
        self.assertEqual([r.column for r in got], ["OrderNo"])

    def test_empty_keyword_returns_nothing(self) -> None:
        self.assertEqual(matcher.filter_columns(self.refs, "", F, True), [])


class TestIterUniqueObjects(unittest.TestCase):
    def test_dedupes_and_preserves_order(self) -> None:
        refs = [
            ref("A", "T1"),
            ref("B", "T1"),
            ref("A", "T2"),
            ref("C", "T1", "sales"),
            ref("D", "T1", "dbo", "VIEW"),
        ]
        got = list(matcher.iter_unique_objects(refs))
        self.assertEqual(
            got,
            [
                ("dbo", "T1", "BASE TABLE"),
                ("dbo", "T2", "BASE TABLE"),
                ("sales", "T1", "BASE TABLE"),
                ("dbo", "T1", "VIEW"),
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
